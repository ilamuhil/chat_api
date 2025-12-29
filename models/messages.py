from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer,String,DateTime  
from typing import Literal
from datetime import datetime, timezone

class Base(DeclarativeBase):
  pass


class Message(Base):
  __tablename__ = "messages"
  id:Mapped[str] = mapped_column(String,primary_key=True)
  conversation_id:Mapped[str] = mapped_column(String,index=True)
  role:Mapped[Literal["user","assistant","system"]] = mapped_column(String,index=True)
  content:Mapped[str] = mapped_column(String,index=True)
  content_type:Mapped[Literal["text","file"]] = mapped_column(String,index=True)
  created_at:Mapped[datetime] = mapped_column(DateTime,index=True,default=datetime.now(timezone.utc))
  
  