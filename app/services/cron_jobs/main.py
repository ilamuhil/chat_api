from rq import cron

from app.services.cron_jobs.conversations import close_stale_conversations

cron.register(close_stale_conversations, queue_name="default", interval=900)
