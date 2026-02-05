File upload flow

1. Send files to API call: /training/upload/init

  a. Create training source
      - Status "pending" with computed content hash
  b. Upload files

---------

Retry mechanism: use the same init API

   c. Compute hash for all the files.
   d. Check if training sources exist for each of the files, matching the hash.
      - If exists, return existing training source id
      - Else, do 1a.

2. Update database

   Check if file exists in storage for each of the training sources:

   - If yes:
       - DB mutation wrapped in a transaction:
           a. Update training source with "pending" status to "created" for the uploaded file
           b. Create file record with status "uploaded"

   - Else:
       a. Update training source status to "update failed" for non-existent files in storage

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

