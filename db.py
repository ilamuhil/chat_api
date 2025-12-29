from sqlalchemy import create_engine, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os
load_dotenv()

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