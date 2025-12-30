import uuid
from db import SessionLocal
from models import Messages, Documents, Embeddings, TrainingJobs
from datetime import datetime, timezone

message  = Messages(id=uuid.uuid4(),conversation_id=uuid.uuid4(),created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc),role="user",content="Hello, how are you?")

database = None
try:
    database = SessionLocal()
    database.add(message)
    database.commit()
    print("Message created successfully")
except Exception as e:
  print(f"Error: {e}")
finally:
  if database is not None:
    database.close()