from rq import cron

from app.services.billing_maintenance import run_billing_maintenance
from app.services.cron_jobs.conversations import close_stale_conversations

cron.register(close_stale_conversations, queue_name="default", interval=900)

cron.register(run_billing_maintenance, queue_name="default", interval=300)
