from __future__ import annotations

import uuid

from rq import Queue

from app.infra.redis_client import redis_client
from app.services.worker_fns import process_training_job


def main() -> None:
    """
    Smoke test for RQ + Redis.

    1) Start Redis (e.g. docker redis:7 mapping 6379).
    2) In one terminal:   rq worker default
    3) In another:       python -m app.scripts.rq_smoke_test
    """
    q = Queue("default", connection=redis_client)
    job_id = str(uuid.uuid4())

    job = q.enqueue(process_training_job, job_id, 123, "org_test", ["src_a", "src_b"])
    print("Enqueued RQ job:", job.id)
    print("Worker should print the payload when it processes the job.")


if __name__ == "__main__":
    main()





