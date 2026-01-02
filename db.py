from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from env_loader import load_app_env

load_app_env()

DB_CONFIG = {
  "host": os.getenv("DB_HOST"),
  "port": os.getenv("DB_PORT"),
  "user": os.getenv("DB_USER"),
  "password": os.getenv("DB_PASSWORD"),
  "name": os.getenv("DB_NAME")
}

for key,value in DB_CONFIG.items():
  if value is None:
    print(f"Environment variable {key} is not set")
    raise Exception(f"Environment variable {key} is not set")

DB_URL = f"postgresql+psycopg://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['name']}?sslmode=require"

engine = create_engine(DB_URL,echo=True,pool_size=10,max_overflow=20,pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False,autoflush=False,bind=engine)


def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()