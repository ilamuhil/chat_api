from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.api.routes.training import delete_training_source
from app.ingestion.cleanup import delete_training_source_job
from app.models.chat_db_models import TrainingJobs
from app.models.dashboard_db_models import TrainingSources


def scalar_result(value):
    result = MagicMock()
    result.one_or_none.return_value = value
    return result


class SourceDeletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source_id, self.bot_id, self.document_id = [uuid4() for _ in range(3)]
        self.organization_id = "test-org"
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        metadata = MetaData()
        self.documents = Table(
            "documents",
            metadata,
            Column("id", Uuid, primary_key=True),
            Column("source_id", Uuid),
            Column("bot_id", Uuid),
            Column("organization_id", String),
            Column("is_active", Boolean),
            Column("deleted_at", DateTime(timezone=True)),
        )
        self.embeddings = Table(
            "embeddings",
            metadata,
            Column("document_id", Uuid, primary_key=True),
            Column("deleted_at", DateTime(timezone=True)),
        )
        metadata.create_all(self.connection)
        # Other tenant/bot rows must survive even if they reuse a source identifier.
        self.connection.execute(
            self.documents.insert(),
            [
                dict(
                    id=self.document_id,
                    source_id=self.source_id,
                    bot_id=self.bot_id,
                    organization_id=self.organization_id,
                    is_active=True,
                ),
                dict(
                    id=uuid4(),
                    source_id=self.source_id,
                    bot_id=self.bot_id,
                    organization_id="other-org",
                    is_active=True,
                ),
                dict(
                    id=uuid4(),
                    source_id=self.source_id,
                    bot_id=uuid4(),
                    organization_id=self.organization_id,
                    is_active=True,
                ),
            ],
        )
        self.connection.execute(
            self.embeddings.insert(), {"document_id": self.document_id}
        )
        self.connection.commit()
        self.source = TrainingSources(
            id=self.source_id,
            bot_id=self.bot_id,
            organization_id=self.organization_id,
            status="trained",
            type="url",
            source_value="https://example.invalid",
            error_message=[],
            deleted_at=None,
        )
        self.config, self.bot_config = (
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4()),
        )
        self.events = []
        self.job = None
        self.chat = MagicMock(spec=AsyncSession)
        self.chat.scalars.side_effect = lambda query: scalar_result(
            self.job
            if query.column_descriptions[0]["entity"] is TrainingJobs
            else self.config
            if query.column_descriptions[0]["entity"].__name__
            == "EmbeddingConfigurations"
            else self.bot_config
        )
        self.chat.add.side_effect = lambda job: setattr(self, "job", job)
        self.chat.execute.side_effect = self.connection.execute
        self.chat.commit.side_effect = self.commit_chat
        self.chat.rollback.side_effect = self.connection.rollback
        self.dashboard = AsyncMock(spec=AsyncSession)
        self.dashboard.scalars.return_value = scalar_result(self.source)
        self.dashboard.commit.side_effect = lambda: self.events.append(
            "dashboard_commit"
        )
        self.dashboard.rollback.side_effect = self.rollback_dashboard
        self.queue = self.enterContext(patch("app.api.routes.training.Queue"))
        self.queue.return_value.enqueue.side_effect = self.enqueue
        self.request = SimpleNamespace(
            state=SimpleNamespace(
                organization_id=self.organization_id,
                claims={
                    "type": "support_agent",
                    "organization_id": self.organization_id,
                    "bot_id": str(self.bot_id),
                    "sub": "staff-test",
                },
            )
        )

    def commit_chat(self):
        self.connection.commit()
        self.events.append("chat_commit")

    def rollback_dashboard(self):
        self.source.deleted_at = None
        self.source.deleted_by = None

    def enqueue(self, *args, **kwargs):
        self.assertFalse(self.active())
        self.assertIsNone(self.source.deleted_at)
        self.events.append("enqueue")

    def active(self):
        return self.connection.scalar(
            select(self.documents.c.is_active).where(
                self.documents.c.id == self.document_id
            )
        )

    async def remove(self):
        return await delete_training_source(
            self.source_id, self.request, self.chat, self.dashboard
        )

    async def test_retrieval_is_disabled_before_enqueue_and_dashboard_deletion(self):
        response = await self.remove()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events, ["chat_commit", "enqueue", "dashboard_commit"])
        self.assertFalse(self.active())
        self.assertIsNotNone(self.source.deleted_at)
        self.assertEqual(self.source.deleted_by, "staff-test")
        self.assertEqual(
            self.connection.scalar(
                select(self.documents.c.is_active).where(
                    self.documents.c.organization_id == "other-org"
                )
            ),
            True,
        )
        self.assertEqual(
            self.connection.scalar(
                select(self.documents.c.is_active).where(
                    self.documents.c.bot_id != self.bot_id
                )
            ),
            True,
        )
        self.assertIn("FOR UPDATE", str(self.dashboard.scalars.call_args.args[0]))

    async def test_file_metadata_is_scoped_and_marked_only_after_dispatch(self):
        self.source.type = "file"
        self.source.source_value = "uploads/private-file"
        response = await self.remove()
        self.assertEqual(response.status_code, 200)
        query = self.dashboard.execute.call_args.args[0]
        params = query.compile().params
        self.assertEqual(params["path_1"], self.source.source_value)
        self.assertEqual(params["organization_id_1"], self.organization_id)
        self.assertEqual(params["bot_id_1"], self.bot_id)
        self.assertIn("files.deleted_at IS NULL", str(query))

    async def test_queue_failure_keeps_source_visible_and_knowledge_disabled(self):
        self.queue.return_value.enqueue.side_effect = RedisError("Synthetic outage")
        with self.assertLogs("app.api.routes.training", level="ERROR"):
            response = await self.remove()
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(self.source.deleted_at)
        self.assertFalse(self.active())
        self.dashboard.commit.assert_not_awaited()
        self.assertEqual(self.job.status, "failed")

    async def test_vector_update_failure_does_not_hide_source_or_queue_cleanup(self):
        self.chat.execute.side_effect = RuntimeError("Synthetic DB failure")
        with self.assertLogs("app.api.routes.training", level="ERROR"):
            response = await self.remove()
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(self.source.deleted_at)
        self.assertTrue(self.active())
        self.queue.assert_not_called()
        self.dashboard.commit.assert_not_awaited()

    async def test_dashboard_commit_failure_keeps_knowledge_disabled(self):
        self.dashboard.commit.side_effect = RuntimeError("Synthetic metadata failure")
        with self.assertLogs("app.api.routes.training", level="ERROR"):
            response = await self.remove()
        self.assertEqual(response.status_code, 500)
        self.assertFalse(self.active())
        self.assertIsNone(self.source.deleted_at)

    async def test_processing_sources_cannot_be_removed(self):
        for status in ("processing", "queued_for_training", "training"):
            with self.subTest(status=status):
                self.source.status = status
                self.assertEqual((await self.remove()).status_code, 409)
        self.assertTrue(self.active())
        self.queue.assert_not_called()
        self.chat.execute.assert_not_awaited()

    async def test_unowned_source_is_not_found_before_any_chat_write(self):
        self.dashboard.scalars.return_value = scalar_result(None)
        self.assertEqual((await self.remove()).status_code, 404)
        params = self.dashboard.scalars.call_args.args[0].compile().params
        self.assertEqual(params["organization_id_1"], self.organization_id)
        self.assertEqual(params["bot_id_1"], self.bot_id)
        self.assertEqual(params["id_1"], self.source_id)
        self.chat.execute.assert_not_awaited()
        self.queue.assert_not_called()

    async def test_visitor_cannot_remove_a_source(self):
        self.request.state.claims["type"] = "user"
        self.assertEqual((await self.remove()).status_code, 403)
        self.dashboard.scalars.assert_not_awaited()
        self.chat.execute.assert_not_awaited()
        self.queue.assert_not_called()

    async def test_missing_model_configuration_does_not_hide_source(self):
        self.config = None
        self.assertEqual((await self.remove()).status_code, 409)
        self.assertIsNone(self.source.deleted_at)
        self.assertTrue(self.active())
        self.queue.assert_not_called()

    async def test_legacy_deleted_source_can_be_retried_to_disable_old_vectors(self):
        self.source.deleted_at = datetime.now(UTC)
        self.queue.return_value.enqueue.side_effect = None
        self.assertEqual((await self.remove()).status_code, 200)
        self.assertFalse(self.active())

    async def test_storage_cleanup_failure_cannot_restore_retrieval(self):
        self.source.type = "file"
        self.source.source_value = "uploads/private-file"
        self.assertEqual((await self.remove()).status_code, 200)
        worker_chat, worker_dashboard = MagicMock(), MagicMock()
        worker_chat.scalars.return_value = scalar_result(self.job)
        worker_chat.execute.side_effect = self.connection.execute
        worker_chat.commit.side_effect = self.connection.commit
        worker_chat.rollback.side_effect = self.connection.rollback
        worker_dashboard.scalars.return_value = scalar_result(self.source)
        with (
            patch("app.ingestion.cleanup.SessionLocal", return_value=worker_chat),
            patch(
                "app.ingestion.cleanup.DashboardDbSessionLocal",
                return_value=worker_dashboard,
            ),
            patch("app.ingestion.cleanup.r2_object_exists", return_value=True),
            patch(
                "app.ingestion.cleanup.r2_delete_object",
                side_effect=RuntimeError("Synthetic R2 failure"),
            ),
            self.assertLogs("app.ingestion.cleanup", level="ERROR"),
        ):
            delete_training_source_job(
                str(self.job.id),
                str(self.source_id),
                self.organization_id,
                str(self.bot_id),
            )
        self.assertFalse(self.active())
        self.assertIsNotNone(
            self.connection.scalar(select(self.embeddings.c.deleted_at))
        )
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.source.error_message[-1]["stage"], "cleanup")
        first_query = worker_dashboard.scalars.call_args_list[0].args[0]
        self.assertIn("FOR UPDATE", str(first_query))
        self.assertNotIn("Synthetic R2 failure", json.dumps(self.source.error_message))


if __name__ == "__main__":
    unittest.main()
