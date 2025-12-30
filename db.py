from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from env_loader import load_app_env

load_app_env()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

if None in [DB_HOST,DB_PORT,DB_USER,DB_PASSWORD,DB_NAME]:
  raise Exception("Cannot access environment variables")

DB_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"

engine = create_engine(DB_URL,echo=True,pool_size=10,max_overflow=20,pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False,autoflush=False,bind=engine)


def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()