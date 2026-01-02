from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from datetime import datetime, timezone
from fastapi import WebSocket

class Message(BaseModel):
  content:str = Field(min_length=1)
  role:Literal["user", "assistant", "system"] = Field(default="user")
  content_type:Literal["text","file"] = Field(default="text")
  conversation_id:str = Field(min_length=1)
  timestamp:datetime = Field(default_factory=datetime.now(timezone.utc))
  
  def to_dict(self):
    return self.model_dump()
  
  def to_json(self):
    return self.model_dump_json()
  
  @classmethod
  def user(cls, content:str, conversation_id:str,content_type:Literal["text","file"] = "text"):
    return cls(content=content, role="user", conversation_id=conversation_id, content_type=content_type)
  
  @classmethod
  def assistant(cls,content:str, conversation_id:str,content_type:Literal["text","file"] = "text"):
    return cls(content=content, role="assistant", conversation_id=conversation_id, content_type=content_type)
  
  def to_db_row(self):
    return {
      "content":self.content,
      "role":self.role,
      "content_type":self.content_type,
      "conversation_id":self.conversation_id,
      "created_at":self.timestamp
    }



class ChatSession(BaseModel):
  model_config = ConfigDict(arbitrary_types_allowed=True)
  conversation_id:str = Field(min_length=1)
  organization_id:str = Field(min_length=1)
  # WebSocket objects are runtime-only (not JSON-serializable); exclude from dumps.
  user_socket:WebSocket | None = Field(default=None, exclude=True)
  agent_socket:WebSocket | None = Field(default=None, exclude=True)
  mode:Literal["ai", "human"] = Field(default="ai") 
  
  def agent_connect(self,websocket:WebSocket):
    self.agent_socket = websocket
    self.mode = "human"
    
  def user_connect(self,websocket:WebSocket):
    self.user_socket = websocket
  
  def agent_disconnect(self):
    self.agent_socket = None
    self.mode = "ai"
  
  def user_disconnect(self):
    self.user_socket = None
  
  
    