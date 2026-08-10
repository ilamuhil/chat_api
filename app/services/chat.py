from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import WebSocket
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.edu_agent import InstituteContext
from app.core.env import load_app_env
from app.db.session import create_chat_db_session
from app.domain.chat import ChatSession
from app.helpers.rag import embed_query, retrieve_closest_embeddings
from app.infra.redis_store import set_data
from app.models.chat_db_models import Messages

logger = logging.getLogger(__name__)

MessageRole = Literal["user", "ai", "support_agent", "system"]
ContentType = Literal["text", "file"]


def _persist_message(
    conversation_id: str,
    role: MessageRole,
    content: str,
    content_type: ContentType,
) -> None:
    now = datetime.now(timezone.utc)
    with create_chat_db_session() as chat_db:
        chat_db.add(Messages(
            id=uuid.uuid4(),
            conversation_id=uuid.UUID(str(conversation_id)),
            role=role,
            content_type=content_type,
            content=content,
            updated_at=now,
        ))
        chat_db.commit()

    # Redis stores JSON, so convert the datetime to an ISO-8601 string.
    if content_type == "text":
        try:
            set_data(
                f"conversation:{conversation_id}:meta",
                {
                    "last_snippet": content,
                    "last_message_at": now.isoformat(),
                },
            )
        except Exception:
            logger.exception(
                "Failed to update conversation metadata in Redis",
                extra={"conversation_id": conversation_id},
            )


async def log_message(
    conversation_id: str,
    role: MessageRole,
    content: str,
    content_type: ContentType = "text",
) -> None:
    try:
        await asyncio.to_thread(
            _persist_message,
            conversation_id,
            role,
            content,
            content_type,
        )
        
    except Exception:
        logger.exception(
            "Failed to persist chat message",
            extra={
                "conversation_id": conversation_id,
                "role": role,
                "content_type": content_type,
            },
        )


async def _send_json_safe(socket: WebSocket | None, data: dict[str, Any]) -> None:
    if socket is None:
        return
    await socket.send_json(data)


async def send_to_support_agent(message_data: dict[str, Any], session: ChatSession) -> None:
    await _send_json_safe(session.agent_socket, message_data)


async def send_to_end_user(
    message_data: dict[str, Any],
    session: ChatSession,
    *,
    persist: bool = True,
) -> None:
    await _send_json_safe(session.user_socket, message_data)

    if not persist or message_data.get("type") == "typing":
        return

    content = message_data.get("message") or message_data.get("content")
    if content is None:
        return

    raw_role = message_data.get("role")
    role: MessageRole = (
        raw_role if raw_role in {"user", "ai", "support_agent", "system"} else "system"
    )
    content_type: ContentType = (
        "file" if message_data.get("type") == "file" else "text"
    )
    await log_message(
        session.conversation_id,
        role,
        str(content),
        content_type,
    )


async def respond_with_ai(
    message_data: dict[str, Any],
    bot_pref: dict[str, Any],
    session: ChatSession,
    agent: Any,
) -> None:
    await _send_json_safe(
        session.user_socket,
        {"type": "typing", "from": "system", "is_typing": True},
    )

    try:
        load_app_env()
        user_text = ""
        if isinstance(message_data, dict):
            user_text = str(message_data.get("message") or message_data.get("content") or "")

        config: RunnableConfig = {"configurable": {"thread_id": session.conversation_id}}
        #Retrieve RAG context from the database
        query_vector = await asyncio.to_thread(
            embed_query,
            user_text,
            bot_pref["embedding_model"],
            int(bot_pref["embedding_dimension"]),
        )
        with create_chat_db_session() as chat_db:
            rows = retrieve_closest_embeddings(
                chat_db,
                query_vector,
                uuid.UUID(str(bot_pref["bot_id"])),
                uuid.UUID(str(bot_pref["embedding_configuration_id"])),
                k=int(bot_pref["retrieval_k"]),
                threshold=float(bot_pref["similarity_threshold"]),
            )
            rag_context = "\n\n".join(document.content for _, document in rows)

        response = await agent.ainvoke(
            {"messages": [HumanMessage(content=user_text)]},
            config=config,
            context=InstituteContext(
                bot_prefs=bot_pref,
                rag_context=rag_context
            ),
        )

        last_message = response["messages"][-1]
        answer = last_message.content
        if not answer and hasattr(last_message, "text"):
            answer = last_message.text
        if not answer:
            raise RuntimeError("Agent returned an empty response")

        await send_to_end_user(
            {"type": "message", "message": answer, "role": "ai", "conversation_id": session.conversation_id},
            session,
        )
    except Exception as e:
        logger.exception("Error in respond_with_ai",extra={"error": e})
        await send_to_end_user(
            {"type": "error", "message": f"AI error: {e}", "role": "system", "conversation_id": session.conversation_id},
            session,
        )
    finally:
        await _send_json_safe(
            session.user_socket,
            {"type": "typing", "from": "system", "is_typing": False, "conversation_id": session.conversation_id},
        )


