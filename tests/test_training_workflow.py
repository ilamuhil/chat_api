from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.training import queue_training
from app.ingestion.errors import TrainingFailure, error_records
from app.ingestion.jobs import process_training_job, training_job_failure_callback
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    TrainingJobs,
)
from app.models.dashboard_db_models import TrainingSources


def scalar_result(value=None, *, sources=None):
    result = MagicMock()
    result.first.return_value = value
    result.one.return_value = value
    result.one_or_none.return_value = value
    result.all.return_value = sources or []
    return result


def configuration(bot_id, state="active"):
    return EmbeddingConfigurations(
        id=uuid.uuid4(),
        bot_id=bot_id,
        state=state,
        provider="openai",
        model="text-embedding-3-small",
        dimension=1536,
        min_chunk_tokens=100,
        target_chunk_tokens=400,
        max_chunk_tokens=700,
    )


class TrainingJobTests(unittest.TestCase):
    def setUp(self):
        self.bot_id = uuid.uuid4()
        self.config = configuration(self.bot_id, "draft")
        self.bot_config = BotConfigurations(
            id=uuid.uuid4(), bot_id=self.bot_id, state="draft"
        )
        self.job = TrainingJobs(
            id=uuid.uuid4(),
            bot_id=self.bot_id,
            organization_id="test-org",
            status="queued",
            embedding_configuration_id=self.config.id,
            bot_configuration_id=self.bot_config.id,
            error_message=[],
        )
        self.sources = [
            TrainingSources(
                id=uuid.uuid4(),
                bot_id=self.bot_id,
                organization_id="test-org",
                type=kind,
                source_value=value,
                status="queued_for_training",
                error_message=error_records(
                    TrainingFailure("previous_error", "Previous failure"),
                    stage="worker",
                ),
            )
            for kind, value in [
                ("url", "https://example.com"),
                ("file", "uploads/guide"),
            ]
        ]
        self.chat = MagicMock()
        self.dashboard = MagicMock()
        self.chat.scalars.side_effect = [
            scalar_result(self.job),
            scalar_result(self.bot_config),
        ]

        def dashboard_result(statement):
            value = statement.compile().params.get("id_1")
            if isinstance(value, list):
                return scalar_result(sources=self.sources)
            return scalar_result(
                next((source for source in self.sources if source.id == value), None)
            )

        self.dashboard.scalars.side_effect = dashboard_result

        def execute(statement):
            if (
                statement.table.name == "embedding_configurations"
                and statement.compile().params.get("state") == "draft"
                and self.config.state == "training"
            ):
                self.config.state = "draft"

        self.chat.execute.side_effect = execute
        self.enterContext(
            patch("app.ingestion.jobs.SessionLocal", return_value=self.chat)
        )
        self.enterContext(
            patch(
                "app.ingestion.jobs.DashboardDbSessionLocal",
                return_value=self.dashboard,
            )
        )
        self.enterContext(
            patch("app.ingestion.jobs._get_job_config", return_value=self.config)
        )
        self.pipeline = self.enterContext(patch("app.ingestion.jobs.IngestionPipeline"))
        self.activate = self.enterContext(
            patch("app.ingestion.jobs._activate_configurations")
        )

    def run_job(self, source_ids=None):
        process_training_job(
            str(self.job.id),
            str(self.bot_id),
            "test-org",
            source_ids or [str(s.id) for s in self.sources],
        )

    def test_every_source_uses_new_pipeline_before_configuration_activation(self):
        self.run_job()
        self.assertEqual(self.pipeline.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in self.pipeline.call_args_list], self.sources
        )
        self.assertTrue(all(source.status == "trained" for source in self.sources))
        self.assertTrue(
            all(source.last_trained_at is not None for source in self.sources)
        )
        self.assertTrue(
            all(source.error_message[0]["resolved_at"] for source in self.sources)
        )
        self.assertEqual(self.job.status, "completed")
        self.activate.assert_called_once_with(
            self.chat, self.config.id, self.bot_config.id, self.bot_id
        )
        self.chat.close.assert_called_once()
        self.dashboard.close.assert_called_once()

    def test_partial_failure_marks_source_and_prevents_configuration_activation(self):
        self.pipeline.return_value.run.side_effect = [None, ValueError("empty file")]
        with self.assertLogs("app.ingestion.jobs", level="ERROR"):
            self.run_job()
        self.assertEqual(self.sources[0].status, "trained")
        self.assertEqual(self.sources[1].status, "training_failed")
        self.assertEqual(self.sources[1].error_message[-1]["code"], "empty_content")
        self.assertEqual(len(self.sources[1].error_message), 2)
        self.assertEqual(
            self.job.error_message[0]["source_id"], str(self.sources[1].id)
        )
        self.assertEqual(self.job.status, "partially_completed")
        self.assertEqual(self.config.state, "draft")
        self.activate.assert_not_called()

    def test_missing_requested_source_prevents_activation(self):
        self.run_job([str(source.id) for source in self.sources] + [str(uuid.uuid4())])
        self.assertEqual(self.job.status, "partially_completed")
        self.assertEqual(self.config.state, "draft")
        self.activate.assert_not_called()

    def test_failure_preserves_existing_active_configuration(self):
        self.config.state = "active"
        self.chat.scalars.side_effect = [scalar_result(self.job)]
        self.pipeline.return_value.run.side_effect = [None, ValueError("bad file")]
        with self.assertLogs("app.ingestion.jobs", level="ERROR"):
            self.run_job()
        self.assertEqual(self.config.state, "active")
        self.assertEqual(self.job.status, "partially_completed")
        self.activate.assert_not_called()

    def test_failure_before_pipeline_records_every_selected_source(self):
        failure = TrainingFailure(
            "embedding_configuration_unavailable",
            "The bot's embedding settings are unavailable.",
            stage="configuration",
        )
        with (
            patch("app.ingestion.jobs._get_job_config", side_effect=failure),
            self.assertLogs("app.ingestion.jobs", level="ERROR"),
        ):
            self.run_job()
        self.assertTrue(
            all(source.status == "training_failed" for source in self.sources)
        )
        self.assertTrue(
            all(
                source.error_message[-1]["code"] == failure.code
                for source in self.sources
            )
        )
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.job.error_message[-1]["stage"], "configuration")
        self.pipeline.assert_not_called()

    def test_secondary_error_saving_source_is_also_recorded_on_job(self):
        self.pipeline.return_value.run.side_effect = [None, ValueError("empty file")]
        with (
            patch(
                "app.ingestion.jobs._mark_source_failed",
                side_effect=RuntimeError("database unreachable"),
            ),
            self.assertLogs("app.ingestion.jobs", level="ERROR"),
        ):
            self.run_job()
        self.assertEqual(
            [record["stage"] for record in self.job.error_message],
            ["worker", "source_status"],
        )
        self.assertEqual(
            self.job.error_message[-1]["source_id"], str(self.sources[1].id)
        )

    def test_failure_finalizing_job_makes_affected_sources_retryable(self):
        self.activate.side_effect = RuntimeError("activation database unavailable")
        with self.assertLogs("app.ingestion.jobs", level="ERROR"):
            self.run_job()
        self.assertEqual(self.job.status, "failed")
        self.assertTrue(
            all(source.status == "training_failed" for source in self.sources)
        )
        self.assertTrue(
            all(source.error_message[-1]["stage"] == "job" for source in self.sources)
        )

    def test_rq_timeout_records_unfinished_source_and_preserves_trained_source(self):
        self.sources[0].status = "trained"
        self.chat.__enter__.return_value = self.chat
        self.dashboard.__enter__.return_value = self.dashboard
        self.chat.scalars.side_effect = [scalar_result(self.job)]
        rq_job = MagicMock()
        rq_job.args = (
            str(self.job.id),
            str(self.bot_id),
            "test-org",
            [str(source.id) for source in self.sources],
        )
        rq_job.meta = {}
        training_job_failure_callback(
            rq_job, None, TimeoutError, TimeoutError("worker interrupted"), None
        )
        self.assertEqual(self.sources[0].status, "trained")
        self.assertEqual(self.sources[1].status, "training_failed")
        self.assertEqual(self.sources[1].error_message[-1]["code"], "training_timeout")
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(rq_job.meta["training_errors"][0]["code"], "training_timeout")
        rq_job.save_meta.assert_called_once()

    def test_redis_fallback_failure_still_saves_both_errors_in_database(self):
        self.chat.__enter__.return_value = self.chat
        self.dashboard.__enter__.return_value = self.dashboard
        self.chat.scalars.side_effect = [scalar_result(self.job)]
        rq_job = MagicMock()
        rq_job.args = (
            str(self.job.id),
            str(self.bot_id),
            "test-org",
            [str(source.id) for source in self.sources],
        )
        rq_job.meta = {}
        rq_job.save_meta.side_effect = RedisError("private Redis details")
        with self.assertLogs("app.ingestion.jobs", level="ERROR"):
            training_job_failure_callback(
                rq_job, None, TimeoutError, TimeoutError("worker interrupted"), None
            )
        self.assertEqual(
            [record["code"] for record in self.job.error_message],
            ["training_timeout", "worker_error_reporting_failed"],
        )
        self.assertTrue(
            all(source.status == "training_failed" for source in self.sources)
        )
        self.assertEqual(len(self.sources[0].error_message), 3)


class TrainingQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_id = uuid.uuid4()
        self.source = TrainingSources(
            id=uuid.uuid4(),
            bot_id=self.bot_id,
            status="training_failed",
            error_message=[],
        )
        self.config = configuration(self.bot_id, "draft")
        self.bot_config = BotConfigurations(id=uuid.uuid4(), state="draft")
        self.chat = MagicMock(spec=AsyncSession)
        self.dashboard = MagicMock(spec=AsyncSession)
        calls = iter(
            [
                scalar_result(),
                scalar_result(),
                scalar_result(self.config),
                scalar_result(self.bot_config),
            ]
        )
        self.chat.scalars.side_effect = lambda _statement: (
            next(calls, None) or scalar_result(self.chat.add.call_args.args[0])
        )
        self.dashboard.scalars.return_value = scalar_result(sources=[self.source])
        self.request = MagicMock()
        self.request.state.claims = {"organization_id": "test-org"}
        self.request.json = AsyncMock(return_value={"bot_id": str(self.bot_id)})
        self.queue = self.enterContext(patch("app.api.routes.training.Queue"))

    async def test_queue_retries_failed_source_using_draft_configuration(self):
        def enqueue(task, job_id, bot_id, organization_id, source_ids, **kwargs):
            self.assertIs(task, process_training_job)
            self.assertEqual(self.source.status, "queued_for_training")
            self.assertEqual(kwargs["job_timeout"], 1800)
            self.assertEqual(source_ids, [str(self.source.id)])
            self.dashboard.commit.assert_awaited_once()

        self.queue.return_value.enqueue.side_effect = enqueue
        response = await queue_training(self.request, self.dashboard, self.chat)
        self.assertEqual(response.status_code, 200)
        self.queue.return_value.enqueue.assert_called_once()
        source_query = str(
            self.dashboard.scalars.call_args.args[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        self.assertIn("training_failed", source_query)
        config_query = str(
            self.chat.scalars.call_args_list[2]
            .args[0]
            .compile(compile_kwargs={"literal_binds": True})
        )
        self.assertIn("draft", config_query)
        self.assertNotIn("failed", config_query)

    async def test_enqueue_failure_records_errors_and_marks_source_and_job_failed(self):
        self.queue.return_value.enqueue.side_effect = RedisError("unavailable")
        with self.assertLogs("app.api.routes.training", level="ERROR"):
            response = await queue_training(self.request, self.dashboard, self.chat)
        self.assertEqual(response.status_code, 500)
        self.chat.rollback.assert_awaited_once()
        self.dashboard.rollback.assert_awaited_once()
        queued_job = self.chat.add.call_args.args[0]
        self.assertEqual(queued_job.status, "failed")
        self.assertEqual(queued_job.error_message[0]["stage"], "queue")
        self.assertEqual(self.source.status, "training_failed")
        self.assertEqual(self.source.error_message[0]["stage"], "queue")

    async def test_retry_filters_failed_sources_and_explicit_source_ids(self):
        self.request.json.return_value.update(
            {"retry_failed": True, "source_ids": [str(self.source.id)]}
        )
        response = await queue_training(self.request, self.dashboard, self.chat)
        self.assertEqual(response.status_code, 200)
        query = str(
            self.dashboard.scalars.call_args.args[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        self.assertIn("training_failed", query)
        self.assertNotIn("'created'", query)
        self.assertIn(self.source.id.hex, query)

    async def test_unavailable_requested_source_does_not_train_another_source(self):
        self.request.json.return_value.update({"source_ids": [str(uuid.uuid4())]})
        response = await queue_training(self.request, self.dashboard, self.chat)
        self.assertEqual(response.status_code, 400)
        self.queue.assert_not_called()

    async def test_configuration_error_is_persisted_on_source_before_job_exists(self):
        self.chat.scalars.side_effect = [
            scalar_result(),
            scalar_result(),
            scalar_result(),
        ]
        with self.assertLogs("app.api.routes.training", level="ERROR"):
            response = await queue_training(self.request, self.dashboard, self.chat)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.source.status, "training_failed")
        self.assertEqual(
            self.source.error_message[-1]["code"], "model_configuration_missing"
        )
        self.queue.assert_not_called()

    async def test_existing_job_prevents_another_dispatch(self):
        self.chat.scalars.side_effect = [scalar_result(TrainingJobs(id=uuid.uuid4()))]
        response = await queue_training(self.request, self.dashboard, self.chat)
        self.assertEqual(response.status_code, 409)
        self.queue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
