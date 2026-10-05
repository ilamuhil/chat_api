from __future__ import annotations

import json
import unittest
from typing import cast

import httpx
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.exc import OperationalError

from app.ingestion.errors import (
    TrainingFailure,
    append_errors,
    error_records,
    error_stage,
    resolve_errors,
)
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    TrainingJobs,
)
from app.models.dashboard_db_models import TrainingSources


class TrainingErrorTests(unittest.TestCase):
    def test_multiple_errors_are_json_serializable_and_keep_source_and_job(self):
        records = error_records(
            ExceptionGroup(
                "multiple",
                [
                    ValueError("empty file"),
                    TrainingFailure(
                        "file_metadata_missing",
                        "File information is missing.",
                        stage="storage_lookup",
                    ),
                ],
            ),
            stage="extraction",
            job_id="job",
            source_id="source",
        )
        self.assertEqual(
            [record["code"] for record in records],
            ["empty_content", "file_metadata_missing"],
        )
        self.assertTrue(
            all(
                record["source_id"] == "source" and record["job_id"] == "job"
                for record in records
            )
        )
        self.assertEqual(json.loads(json.dumps(records)), records)

    def test_history_append_deduplicates_same_event_without_mutating_previous_attempt(
        self,
    ):
        old = error_records(
            ValueError("empty file"), stage="extraction", job_id="first"
        )
        new = error_records(
            ValueError("empty file"), stage="extraction", job_id="retry"
        )
        history = append_errors(old, old + new)
        self.assertEqual(len(history), 2)
        self.assertEqual(len(old), 1)
        resolved = resolve_errors(history)
        self.assertTrue(all(record["resolved_at"] for record in resolved))
        self.assertTrue(all(record["resolved_at"] is None for record in history))

    def test_database_error_uses_safe_message_instead_of_query_or_credentials(self):
        error = OperationalError(
            "SELECT password FROM credentials",
            {"password": "secret"},
            RuntimeError("private database host"),
        )
        records = error_records(error, stage="persistence")
        self.assertEqual(records[0]["code"], "database_error")
        self.assertNotIn("secret", json.dumps(records))
        self.assertNotIn("credentials", json.dumps(records))
        self.assertNotIn("private database host", json.dumps(records))

    def test_wrapped_http_error_preserves_user_friendly_root_cause(self):
        response = httpx.Response(
            404, request=httpx.Request("GET", "https://example.com")
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as cause:
            error = ValueError("Unable to fetch HTML")
            error.__cause__ = cause
        record = error_records(error, stage="extraction")[0]
        self.assertEqual(record["code"], "url_fetch_failed")
        self.assertIn("404", record["message"])
        self.assertFalse(record["retryable"])

    def test_stage_wrapper_marks_pipeline_location(self):
        with self.assertRaises(TrainingFailure) as raised:
            with error_stage("download"):
                raise RuntimeError("sensitive provider details")
        self.assertEqual(raised.exception.stage, "download")
        self.assertEqual(raised.exception.code, "download_failed")
        self.assertNotIn("sensitive", str(raised.exception))

    def test_nested_group_keeps_each_failure_and_pipeline_stage(self):
        with self.assertRaises(ExceptionGroup) as raised:
            with error_stage("extraction"):
                raise ExceptionGroup(
                    "parsers",
                    [
                        ValueError("CSV contains duplicate headers"),
                        ExceptionGroup(
                            "nested", [ValueError("CSV contains an empty header")]
                        ),
                    ],
                )
        records = error_records(raised.exception, stage="worker")
        self.assertEqual(
            [record["code"] for record in records],
            ["csv_duplicate_headers", "csv_empty_header"],
        )
        self.assertTrue(all(record["stage"] == "extraction" for record in records))

    def test_csv_failure_reasons_are_distinct_and_clear(self):
        cases = {
            "CSV has no header row": "csv_missing_headers",
            "Malformed CSV at row 3: row contains more values than the header": "csv_malformed_row",
            "CSV field 'Notes' exceeds the maximum allowed size of 700 tokens": "csv_field_too_large",
        }
        for message, code in cases.items():
            with self.subTest(code=code):
                record = error_records(ValueError(message), stage="extraction")[0]
                self.assertEqual(record["code"], code)
                self.assertFalse(record["retryable"])

    def test_error_fields_are_json_and_configuration_states_exclude_failed(self):
        for model in (TrainingSources, TrainingJobs):
            self.assertEqual(
                type(model.__table__.c.error_message.type).__name__, "JSONB"
            )
            self.assertFalse(model.__table__.c.error_message.nullable)
        for model in (EmbeddingConfigurations, BotConfigurations):
            table = cast(Table, model.__table__)
            constraints = [
                str(constraint.sqltext)
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            ]
            self.assertTrue(
                any(
                    "state IN" in constraint and "failed" not in constraint
                    for constraint in constraints
                )
            )


if __name__ == "__main__":
    unittest.main()
