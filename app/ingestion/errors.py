"""Public training errors: safe JSON records shared by sources, jobs, and the UI."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError


class TrainingError(TypedDict):
    id: str
    code: str
    stage: str
    message: str
    action: str
    retryable: bool
    occurred_at: str
    job_id: str | None
    source_id: str | None
    resolved_at: str | None


class TrainingFailure(ValueError):
    """A known failure with a stable code and safe instructions for the user."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "validation",
        action: str = "Retry training. If the issue continues, contact support.",
        retryable: bool = True,
    ):
        super().__init__(message)
        self.code, self.stage, self.action, self.retryable = (
            code,
            stage,
            action,
            retryable,
        )


_STAGE_MESSAGES = {
    "configuration": (
        "The bot's model settings are unavailable or invalid.",
        "Check the bot's model settings, then retry training.",
    ),
    "storage_lookup": (
        "The uploaded file's information could not be read.",
        "Retry training. If the file is still unavailable, upload it again.",
    ),
    "storage_check": (
        "File storage could not be reached to verify the upload.",
        "Retry training when storage is available.",
    ),
    "download": (
        "The uploaded file could not be downloaded.",
        "Retry training. If the issue continues, upload the file again.",
    ),
    "extraction": (
        "The source could not be read or parsed.",
        "Check that the file is readable or the URL is accessible, then retry.",
    ),
    "processing": (
        "The extracted content could not be prepared for training.",
        "Check the source content and the bot's chunk settings, then retry.",
    ),
    "embedding": (
        "Embeddings could not be generated for this source.",
        "Retry training. If the issue continues, check the embedding service settings.",
    ),
    "persistence": (
        "The training documents could not be saved.",
        "Retry training when the database is available.",
    ),
    "source_status": (
        "The source's training result could not be saved.",
        "Retry training to finish saving this source's result.",
    ),
    "queue": (
        "Training could not be queued.",
        "Retry training when the training service is available.",
    ),
    "worker": (
        "Training stopped before this source finished.",
        "Retry the failed sources.",
    ),
    "cleanup": (
        "The source could not be fully removed.",
        "Retry removal. If the issue continues, contact support.",
    ),
    "job": (
        "The training job could not finish.",
        "Retry the failed sources. If the issue continues, contact support.",
    ),
    "validation": (
        "The training source information is invalid.",
        "Check the source details, then retry training.",
    ),
}


def _describe(error: Exception, stage: str) -> TrainingFailure:
    if isinstance(error, TrainingFailure):
        return error
    # Inspect causes for classification; raw database/provider messages stay in logs.
    causes: list[Exception] = []
    current: BaseException | None = error
    while isinstance(current, Exception) and current not in causes:
        causes.append(current)
        current = current.__cause__ or current.__context__
    for cause in causes:
        name = type(cause).__name__
        if isinstance(cause, SQLAlchemyError):
            return TrainingFailure(
                "database_error",
                "The database could not save or load the training data.",
                stage=stage,
                action="Retry when the database is available.",
            )
        if isinstance(cause, UnicodeError):
            return TrainingFailure(
                "invalid_text_encoding",
                "The file's text encoding could not be read.",
                stage=stage,
                action="Save the file as UTF-8 and upload it again.",
                retryable=False,
            )
        if name in {
            "JobTimeoutException",
            "TimeoutError",
            "ReadTimeout",
            "ConnectTimeout",
        }:
            return TrainingFailure(
                "training_timeout",
                "Training timed out while processing this source.",
                stage=stage,
                action="Retry training. For large documents, split the file into smaller files.",
            )
        if name == "RateLimitError":
            return TrainingFailure(
                "embedding_rate_limit",
                "The embedding service has reached its request or usage limit.",
                stage=stage,
                action="Check the service quota and retry training.",
            )
        if name in {"APIConnectionError", "APITimeoutError"}:
            return TrainingFailure(
                "embedding_connection_failed",
                "The embedding service could not be reached or did not respond in time.",
                stage=stage,
                action="Retry when the embedding service is available.",
            )
        if name == "BadRequestError" and stage == "embedding":
            return TrainingFailure(
                "embedding_request_rejected",
                "The embedding service rejected the model settings or source content.",
                stage=stage,
                action="Check the embedding model, dimensions and content limits, then retry.",
                retryable=False,
            )
        if name == "ClientError":
            response = getattr(cause, "response", None)
            storage_code = (
                response.get("Error", {}).get("Code")
                if isinstance(response, dict)
                else None
            )
            if storage_code in {"NoSuchKey", "NoSuchBucket", "404"}:
                return TrainingFailure(
                    "storage_file_missing",
                    "The uploaded file could not be found in storage.",
                    stage=stage,
                    action="Upload the file again.",
                    retryable=False,
                )
            if storage_code in {
                "AccessDenied",
                "InvalidAccessKeyId",
                "SignatureDoesNotMatch",
            }:
                return TrainingFailure(
                    "storage_access_denied",
                    "The file storage service rejected access to the uploaded file.",
                    stage=stage,
                    action="Ask an administrator to check the storage credentials and permissions, then retry.",
                    retryable=False,
                )
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return TrainingFailure(
                "embedding_access_denied",
                "The embedding service rejected the configured credentials.",
                stage=stage,
                action="Ask an administrator to update the embedding service credentials, then retry.",
                retryable=False,
            )
        if name == "HTTPStatusError":
            response = getattr(cause, "response", None)
            raw_status = getattr(response, "status_code", None)
            status = raw_status if isinstance(raw_status, int) else None
            status_label = str(status) if status is not None else "unknown"
            return TrainingFailure(
                "url_fetch_failed",
                f"The website returned an HTTP {status_label} error.",
                stage=stage,
                action="Check that the URL opens without signing in, then retry.",
                retryable=status is not None and (status >= 500 or status == 429),
            )
    # Translate known parser failures without exposing source text or infrastructure details.
    text = " ".join(str(cause).lower() for cause in causes)
    if "embedding response does not match" in text:
        return TrainingFailure(
            "invalid_embedding_response",
            "The embedding service returned incomplete vectors or incorrect vector dimensions.",
            stage=stage,
            action="Retry training. If it happens again, check the embedding model and dimension settings.",
        )
    if (
        "empty" in text or "200 characters" in text or "no usable knowledge" in text
    ) and "empty header" not in text:
        return TrainingFailure(
            "empty_content",
            "The source contains no usable content, or too little readable content to train.",
            stage=stage,
            action="Add readable content or upload a different file, then retry.",
            retryable=False,
        )
    if "url must" in text or "invalid port" in text:
        return TrainingFailure(
            "invalid_url",
            "The URL must be a valid HTTP or HTTPS address.",
            stage=stage,
            action="Correct the URL and retry training.",
            retryable=False,
        )
    if "html content" in text:
        return TrainingFailure(
            "unsupported_url_content",
            "The URL does not return a supported HTML page.",
            stage=stage,
            action="Use a webpage URL or upload the document as a file.",
            retryable=False,
        )
    if "csv" in text and any(
        word in text for word in ("header", "malformed", "tokens")
    ):
        row = re.search(r"row (\d+)", text)
        if "duplicate headers" in text:
            code, message = (
                "csv_duplicate_headers",
                "The CSV has duplicate column headers. Each column needs a unique name.",
            )
        elif "empty header" in text:
            code, message = (
                "csv_empty_header",
                "The CSV contains a column without a header.",
            )
        elif "no header row" in text:
            code, message = (
                "csv_missing_headers",
                "The CSV is missing its column header row.",
            )
        elif "tokens" in text:
            code, message = (
                "csv_field_too_large",
                "A CSV field exceeds the configured chunk size.",
            )
        else:
            code, message = (
                "csv_malformed_row",
                f"The CSV has too many values at row {row[1]}."
                if row
                else "The CSV contains a malformed row.",
            )
        return TrainingFailure(
            code,
            message,
            stage=stage,
            action="Correct the CSV headers or rows and upload the file again.",
            retryable=False,
        )
    if "conversion" in text or "convert pdf" in text:
        return TrainingFailure(
            "document_conversion_failed",
            "The document could not be converted into readable content.",
            stage=stage,
            action="Check whether the file is damaged or protected. Export a new copy and upload it again.",
            retryable=False,
        )
    if "max tokens" in text or "max_tokens" in text:
        return TrainingFailure(
            "content_too_large",
            "Some source content exceeds the configured chunk limit.",
            stage=stage,
            action="Split the large table or text block, or update the chunk limits, then retry.",
            retryable=False,
        )
    if "unclosed markdown" in text or "table cell" in text or "token has no" in text:
        return TrainingFailure(
            "invalid_document_structure",
            "The document contains a structure that could not be parsed.",
            stage=stage,
            action="Check the lists and tables, or export a fresh copy of the document, then retry.",
            retryable=False,
        )
    message, action = _STAGE_MESSAGES.get(stage, _STAGE_MESSAGES["job"])
    return TrainingFailure(f"{stage}_failed", message, stage=stage, action=action)


@contextmanager
def error_stage(stage: str) -> Iterator[None]:
    try:
        yield
    except TrainingFailure:
        raise
    except ExceptionGroup as errors:

        def describe_group(error: Exception) -> Exception:
            if isinstance(error, ExceptionGroup):
                return error.derive(
                    [describe_group(child) for child in error.exceptions]
                )
            return _describe(error, stage)

        raise describe_group(errors) from errors
    except Exception as error:
        raise _describe(error, stage) from error


def error_records(
    error: Exception,
    *,
    stage: str,
    job_id: str | None = None,
    source_id: str | None = None,
) -> list[TrainingError]:
    if isinstance(error, ExceptionGroup):
        return [
            record
            for child in error.exceptions
            for record in error_records(
                child, stage=stage, job_id=job_id, source_id=source_id
            )
        ]
    failure = _describe(error, stage)
    return [
        {
            "id": str(uuid4()),
            "code": failure.code,
            "stage": failure.stage,
            "message": str(failure),
            "action": failure.action,
            "retryable": failure.retryable,
            "occurred_at": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "source_id": source_id,
            "resolved_at": None,
        }
    ]


def append_errors(
    existing: Sequence[Mapping[str, Any]] | None, incoming: list[TrainingError]
) -> list[dict[str, Any]]:
    history = [dict(deepcopy(record)) for record in existing or []]
    ids = {record.get("id") for record in history}
    return history + [
        dict(deepcopy(record)) for record in incoming if record["id"] not in ids
    ]


def resolve_errors(existing: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    history = deepcopy(existing or [])
    now = datetime.now(UTC).isoformat()
    for record in history:
        if not record.get("resolved_at"):
            record["resolved_at"] = now
    return history
