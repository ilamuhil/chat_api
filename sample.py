import uuid
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.python_chat import Messages


def main() -> None:
    if SessionLocal is None:
        raise RuntimeError("DB is not configured. Set DB_* env vars before running this script.")

    db = SessionLocal()
    try:
        msg = Messages(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            role="user",
            content="hello",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(msg)
        db.commit()
        print("Inserted message:", msg.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()