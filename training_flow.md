Training and file-upload flow notes

This document describes the dashboard/API hand-off expected by the training
worker. The current Chat API exposes queue/delete training operations; upload
initialization and object-upload endpoints are owned by the dashboard service.
Keep the status values below aligned with both services.

1. The dashboard service initializes a file upload and creates a training
   source.

  a. Create training source
      - Status `pending` with a computed content hash
  b. Upload files

---------

Retry mechanism: use the same init API

   c. Compute hash for all the files.
   d. Check if training sources exist for each of the files, matching the hash.
      - If exists, return existing training source id
      - Else, do 1a.

2. Finalize the upload and update the dashboard database.

   Check if file exists in storage for each of the training sources:

   - If yes:
       - DB mutation wrapped in a transaction:
           a. Update the training source from `pending` to `created`
           b. Create a file record with status `uploaded`

   - Else:
       a. Update the training source status to `upload_failed` when the object
          is missing from storage

Training source statuses (phase 1):

pending        → intent created, upload expected  
created        → file verified + file record created  
upload_failed  → upload never completed

File records statuses (phase 1):

"uploaded"     → file verified


Risks 
- upload succeeds and finalize api not called / user abandons flow midway (orphaned files and training source records) -> can be cleaned up by worker



| Table name    | Status                | When to update                         |
| ------------- | --------------------- | -------------------------------------- |
| training_jobs | `queued`              | Job created                            |
| training_jobs | `processing`          | Worker starts job                      |
| training_jobs | `completed`           | All sources trained successfully       |
| training_jobs | `partially_completed` | Some sources trained, some failed      |
| training_jobs | `failed`              | Job fails before any source is trained |
| training_jobs | `cleanup_completed`   | Cleanup for soft-deleted sources done  |


| Table name       | Status            | When to update                             |
| ---------------- | ----------------- | ------------------------------------------ |
| training_sources | `pending`         | Source record created                      |
| training_sources | `created`         | Upload / fetch initiated                   |
| training_sources | `upload_failed`   | Upload or fetch fails
| training_sources | `queued_for_training` | When the job is enqueued successfully                      |
| training_sources | `training`        | Training job starts processing this source |
| training_sources | `trained`         | Source successfully embedded               |
| training_sources | `training_failed` | Training fails for this source             |


| Table name | Status              | When to update           |
| ---------- | ------------------- | ------------------------ |
| files      | `uploaded`          | Upload completes         |
| files      | `processing`        | Text extraction starts   |
| files      | `processed`         | Text extraction succeeds |
| files      | `processing_failed` | Text extraction fails    |



The worker entry point is `app.ingestion.jobs.process_training_job`. Each source
runs through `app.ingestion.pipeline.IngestionPipeline`: format-specific
extraction/parsing → knowledge-unit cleaning, splitting and merging → document
adaptation with source/structure metadata → embedding generation. Chunk limits
come from `min_chunk_tokens`, `target_chunk_tokens`, and `max_chunk_tokens` on the
job's embedding configuration. URL extraction uses the HTML pipeline; uploaded
PDF, DOCX, CSV, Markdown, TXT, and HTML files use their corresponding new parsers.

Document replacement and embedding creation share one Chat DB transaction per
source. A failure rolls back both and preserves the previous source/configuration
rows. The source becomes `training_failed`; successful sources become `trained`,
mark previous errors resolved, and update `last_trained_at`. Failed sources are
eligible for another queue request. New configurations return to `draft` after
an unsuccessful run; active configurations stay active. Deleted or missing
requested sources prevent configuration activation.

The queue endpoint records `queued_for_training` before dispatching the worker,
and records a structured error with `training_failed` on affected sources if
enqueueing fails. The worker receives a
30-minute timeout for Docling extraction/OCR and embedding. Existing queued jobs
using the removed `app.services.worker_fns` import path must be drained before
deploying this version. Cleanup uses `app.ingestion.cleanup.delete_training_source_job`.


Source and job errors are JSONB arrays, retaining every attempt. Each entry has
an ID, stable code, stage, user-facing message, suggested action, retryability,
timestamp, source/job IDs, and a resolution timestamp. Exceptions before source
processing are recorded on all selected unfinished sources. Source failures and
secondary reporting errors are aggregated on the job. SQL and provider internals
are logged separately from public messages.

A retry request can select `source_ids` and set `retry_failed: true`; trained
sources are excluded. The dashboard displays error histories and provides both
per-source retry and retry-all-failed controls. Job outcomes are `completed`,
`partially_completed`, or `failed`, independent of configuration readiness.

Apply Dashboard DB changes only through Prisma in the chat-dashboard repository
(`20261005123000_structured_training_errors`). Apply Chat DB changes with Alembic
(`6e40a12bc893`). Neither migration tool manages the other database.
