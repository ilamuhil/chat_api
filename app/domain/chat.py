from __future__ import annotations

from typing import Literal

from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict, Field


class ChatSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    conversation_id: str = Field(min_length=1)
    visitor_id: str | None = Field(default=None)
    organization_id: str = Field(min_length=1)
    # WebSocket objects are runtime-only (not JSON-serializable); exclude from dumps.
    user_socket: WebSocket | None = Field(default=None, exclude=True)
    agent_socket: WebSocket | None = Field(default=None, exclude=True)
    agent_user_id: str | None = None
    end_chat_requested: bool = False
    mode: Literal["ai", "human"] = Field(default="ai")

    def agent_connect(self, websocket: WebSocket, user_id: str | None = None):
        self.agent_socket = websocket
        self.agent_user_id = user_id
        self.end_chat_requested = False
        self.mode = "human"

    def user_connect(self, websocket: WebSocket):
        self.user_socket = websocket

    def agent_disconnect(self):
        self.agent_socket = None
        self.agent_user_id = None
        self.end_chat_requested = False
        self.mode = "ai"

    def user_disconnect(self):
        self.user_socket = None
