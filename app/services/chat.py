from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import WebSocket
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.core.env import load_app_env
from app.domain.chat import ChatSession

logger = logging.getLogger(__name__)

async def _send_json_safe(socket: WebSocket | None, data: dict[str, Any]) -> None:
    if socket is None:
        return
    await socket.send_json(data)


async def send_to_support_agent(message_data: dict[str, Any], session: ChatSession) -> None:
    await _send_json_safe(session.agent_socket, message_data)


async def send_to_end_user(message_data: dict[str, Any], session: ChatSession) -> None:
    await _send_json_safe(session.user_socket, message_data)


async def respond_with_ai(message_data: dict[str, Any], session: ChatSession, agent:CompiledStateGraph) -> None:
    await _send_json_safe(
        session.user_socket,
        {"type": "typing", "from": "assistant", "is_typing": True, "conversation_id": session.conversation_id},
    )

    try:
        load_app_env()
        user_text = ""
        if isinstance(message_data, dict):
            user_text = str(message_data.get("message") or message_data.get("content") or "")

        config: RunnableConfig = {"configurable": {"thread_id": session.conversation_id}}
        response = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_text)]},
            config=config,
        )

        last_message = response["messages"][-1]
        answer = last_message.content
        if not answer and hasattr(last_message, "text"):
            answer = last_message.text
        if not answer:
            raise RuntimeError("Agent returned an empty response")

        await _send_json_safe(
            session.user_socket,
            {"type": "message", "message": answer, "role": "assistant", "conversation_id": session.conversation_id},
        )
    except Exception as e:
        logger.exception("Error in respond_with_ai",extra={"error": e})
        await _send_json_safe(
            session.user_socket,
            {"type": "error", "message": f"AI error: {e}", "conversation_id": session.conversation_id},
        )
    finally:
        await _send_json_safe(
            session.user_socket,
            {"type": "typing", "from": "assistant", "is_typing": False, "conversation_id": session.conversation_id},
        )


