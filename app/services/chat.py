from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import WebSocket
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy import update

from app.core.env import load_app_env
from app.db.session import (
    create_async_chat_db_session,
    create_async_dashboard_db_session,
)
from app.domain import ChatSession, InstituteContext
from app.infra.redis_store import set_data
from app.models.chat_db_models import Messages, RetrievalLogs
from app.models.dashboard_db_models import ConversationsMeta
from app.rag import (
    RetrievalPipeline,
    RetrievalRequest,
    RetrievalResult,
    RetrievalResultStatus,
)
from app.rag.shared_dataclasses import ConversationTurn, PreparedQuery, RetrievalFailure

logger = logging.getLogger(__name__)

MessageRole = Literal["user", "ai", "support_agent", "system"]
ContentType = Literal["text", "file"]

INSUFFICIENT_CONTEXT_MESSAGE = (
    "I don't have confirmed information about that. "
    "Would you like me to connect you with an admissions counsellor?"
)
EMPTY_AGENT_RESPONSE_ERROR = (
    "I couldn't generate a reply for that just now. "
    "Please try again, or ask me to connect you with an admissions counsellor."
)


def _extract_message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        text = "".join(parts).strip()
        if text:
            return text
    text = str(getattr(message, "text", "") or "").strip()
    return text


def _typing_payload(session: ChatSession, is_typing: bool) -> dict[str, Any]:
    return {
        "type": "typing",
        "from": "system",
        "is_typing": is_typing,
        "conversation_id": session.conversation_id,
    }


async def send_typing_to_end_user(session: ChatSession, is_typing: bool) -> None:
    await _send_json_safe(session.user_socket, _typing_payload(session, is_typing))


async def send_typing_to_support_agent(session: ChatSession, is_typing: bool) -> None:
    await _send_json_safe(session.agent_socket, _typing_payload(session, is_typing))


@asynccontextmanager
async def end_user_typing(session: ChatSession) -> AsyncIterator[None]:
    """Send typing=True, then always follow up with typing=False."""
    await send_typing_to_end_user(session, True)
    try:
        yield
    finally:
        await send_typing_to_end_user(session, False)


async def _persist_message(
    conversation_id: str,
    role: MessageRole,
    content: str,
    content_type: ContentType,
) -> None:
    now = datetime.now(UTC)
    async with (
        create_async_chat_db_session() as chat_db,
        create_async_dashboard_db_session() as dashboard_db,
    ):
        chat_db.add(
            Messages(
                id=uuid.uuid4(),
                conversation_id=uuid.UUID(str(conversation_id)),
                role=role,
                content_type=content_type,
                content=content,
                updated_at=now,
            )
        )
        stmnt = (
            update(ConversationsMeta)
            .where(
                ConversationsMeta.id == uuid.UUID(str(conversation_id)),
            )
            .values(
                last_message_at=now,
            )
        )
        await dashboard_db.execute(stmnt)
        await chat_db.commit()
        await dashboard_db.commit()

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
        await _persist_message(
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


async def _persist_retrieval_log(
    *,
    organization_id: str,
    bot_id: uuid.UUID,
    conversation_id: uuid.UUID,
    query: str,
    result: RetrievalResult,
    retrieval_threshold: float,
    retrieval_k: int,
    embedding_configuration_id: uuid.UUID,
    llm_configuration_id: uuid.UUID | None,
    message_id: uuid.UUID | None = None,
    reranker_used: bool = False,
) -> None:
    candidates = result.diagnostics.get("candidates", [])
    document_ids = [uuid.UUID(item["document_id"]) for item in candidates]
    # Keyword-only candidates have no cosine similarity; retain NULL alignment.
    similarity_scores = [item["semantic_score"] for item in candidates]
    bundle = result.context_bundle
    async with create_async_chat_db_session() as chat_db:
        chat_db.add(
            RetrievalLogs(
                id=uuid.uuid4(),
                organization_id=organization_id,
                bot_id=bot_id,
                conversation_id=conversation_id,
                message_id=message_id,
                query=query,
                retrieved_document_ids=document_ids,
                similarity_scores=similarity_scores,
                retrieval_threshold=retrieval_threshold,
                retrieval_k=retrieval_k,
                reranker_used=reranker_used,
                embedding_configuration_id=embedding_configuration_id,
                llm_configuration_id=llm_configuration_id,
                reranked_document_ids=None,
                details={
                    "status": result.status.value,
                    "prepared_query": result.prepared_query.model_dump(mode="json"),
                    "diagnostics": result.diagnostics,
                    "selected_document_ids": [
                        str(item.candidate.document_id)
                        for item in bundle.selected_candidates
                    ]
                    if bundle
                    else [],
                    "source_references": [
                        ref.model_dump(mode="json") for ref in bundle.source_references
                    ]
                    if bundle
                    else [],
                },
            )
        )
        await chat_db.commit()


async def log_retrieval(
    *,
    organization_id: str,
    bot_id: uuid.UUID,
    conversation_id: uuid.UUID,
    query: str,
    result: RetrievalResult,
    retrieval_threshold: float,
    retrieval_k: int,
    embedding_configuration_id: uuid.UUID,
    llm_configuration_id: uuid.UUID | None,
    message_id: uuid.UUID | None = None,
    reranker_used: bool = False,
) -> None:
    try:
        await _persist_retrieval_log(
            organization_id=organization_id,
            bot_id=bot_id,
            conversation_id=conversation_id,
            query=query,
            result=result,
            retrieval_threshold=retrieval_threshold,
            retrieval_k=retrieval_k,
            embedding_configuration_id=embedding_configuration_id,
            llm_configuration_id=llm_configuration_id,
            message_id=message_id,
            reranker_used=reranker_used,
        )
    except Exception:
        logger.exception(
            "Failed to persist retrieval log",
            extra={
                "conversation_id": str(conversation_id),
                "bot_id": str(bot_id),
            },
        )


async def _send_json_safe(socket: WebSocket | None, data: dict[str, Any]) -> None:
    if socket is None:
        return
    try:
        await socket.send_json(data)
    except RuntimeError:
        # The peer can disconnect between the socket check and this send.
        # Cleanup notifications, such as typing=False, must not mask that
        # disconnect or fail the request.
        logger.debug("Socket already closed while sending", exc_info=True)


async def send_to_support_agent(
    message_data: dict[str, Any], session: ChatSession
) -> None:
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
    content_type: ContentType = "file" if message_data.get("type") == "file" else "text"
    await log_message(
        session.conversation_id,
        role,
        str(content),
        content_type,
    )


async def _retrieval_history(
    agent: Any, config: RunnableConfig
) -> list[ConversationTurn]:
    try:
        snapshot = await agent.aget_state(config)
        turns: list[ConversationTurn] = []
        for message in snapshot.values.get("messages", []):
            role = getattr(message, "type", None)
            if role not in {"human", "ai"}:
                continue
            content = _extract_message_text(message)
            if content:
                turns.append(
                    ConversationTurn(
                        role="user" if role == "human" else "assistant", content=content
                    )
                )
        return turns
    except Exception:
        logger.exception("Conversation history unavailable for query preparation")
        return []


async def respond_with_ai(
    message_data: dict[str, Any],
    bot_pref: dict[str, Any],
    session: ChatSession,
    agent: Any,
) -> None:
    async with end_user_typing(session):
        try:
            load_app_env()
            user_text = ""
            if isinstance(message_data, dict):
                user_text = str(
                    message_data.get("message") or message_data.get("content") or ""
                )

            config: RunnableConfig = {
                "configurable": {"thread_id": session.conversation_id}
            }
            bot_id = uuid.UUID(str(bot_pref["bot_id"]))
            embedding_configuration_id = uuid.UUID(
                str(bot_pref["embedding_configuration_id"])
            )
            llm_configuration_id = uuid.UUID(str(bot_pref["bot_configuration_id"]))
            # Read the checkpoint BEFORE adding the current request. This excludes
            # later messages already queued/persisted by the WebSocket reader.
            history = await _retrieval_history(agent, config)
            request = RetrievalRequest(
                original_message=user_text,
                organization_id=session.organization_id,
                bot_id=bot_id,
                embedding_configuration_id=embedding_configuration_id,
                bot_configuration_id=llm_configuration_id,
                scoped_conversation_history=history,
            )
            try:
                async with create_async_chat_db_session() as chat_db:
                    retrieval = await RetrievalPipeline(chat_db).retrieve(request)
            except Exception as error:
                logger.exception(
                    "Hybrid retrieval unavailable",
                    extra={"conversation_id": session.conversation_id},
                )
                # Keep counsellor requests and non-factual conversation available.
                retrieval = RetrievalResult(
                    status=RetrievalResultStatus.UNAVAILABLE,
                    prepared_query=PreparedQuery(
                        original_message=user_text,
                        standalone_query=user_text,
                        did_rewrite=False,
                    ),
                    diagnostics={
                        "error": {
                            "code": error.code
                            if isinstance(error, RetrievalFailure)
                            else "retrieval_unavailable",
                            "stage": error.stage
                            if isinstance(error, RetrievalFailure)
                            else "retrieval",
                            "message": error.message
                            if isinstance(error, RetrievalFailure)
                            else "Reference information could not be searched. Please try again shortly.",
                            "action": "Retry the request or contact a counsellor.",
                        }
                    },
                )
            bundle = retrieval.context_bundle
            has_evidence = bool(bundle and bundle.selected_candidates)
            await log_retrieval(
                organization_id=session.organization_id,
                bot_id=bot_id,
                conversation_id=uuid.UUID(str(session.conversation_id)),
                query=retrieval.prepared_query.standalone_query,
                result=retrieval,
                retrieval_threshold=float(
                    retrieval.diagnostics.get(
                        "similarity_threshold", bot_pref["similarity_threshold"]
                    )
                ),
                retrieval_k=int(
                    retrieval.diagnostics.get("retrieval_k", bot_pref["retrieval_k"])
                ),
                embedding_configuration_id=embedding_configuration_id,
                llm_configuration_id=llm_configuration_id,
            )

            logger.info(
                "Starting agent invocation",
                extra={
                    "conversation_id": str(session.conversation_id),
                    "message_length": len(user_text),
                    "has_rag_context": has_evidence,
                },
            )
            response = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_text)]},
                config=config,
                context=InstituteContext(
                    bot_prefs=bot_pref,
                    retrieval_result=retrieval,
                    conversation_id=uuid.UUID(str(session.conversation_id)),
                ),
            )

            response_messages = response.get("messages", [])
            tool_calls = [
                tool_call.get("name")
                for message in response_messages
                for tool_call in (getattr(message, "tool_calls", None) or [])
                if isinstance(tool_call, dict)
            ]
            logger.info(
                "Agent invocation completed",
                extra={
                    "conversation_id": str(session.conversation_id),
                    "message_count": len(response_messages),
                    "tool_calls": tool_calls,
                },
            )
            last_message = response_messages[-1] if response_messages else None
            answer = _extract_message_text(last_message)
            if not answer:
                fallback = (
                    INSUFFICIENT_CONTEXT_MESSAGE
                    if not has_evidence
                    and retrieval.status != RetrievalResultStatus.UNAVAILABLE
                    else EMPTY_AGENT_RESPONSE_ERROR
                )
                logger.warning(
                    "Agent returned an empty response; sending fallback message",
                    extra={
                        "conversation_id": session.conversation_id,
                        "message_type": type(last_message).__name__,
                        "had_rag_context": has_evidence,
                    },
                )
                await send_to_end_user(
                    {
                        "type": "message",
                        "message": fallback,
                        "retrieval_status": retrieval.status.value,
                        "sources": [],
                        "role": "ai",
                        "conversation_id": session.conversation_id,
                    },
                    session,
                )
                return

            await send_to_end_user(
                {
                    "type": "message",
                    "message": answer,
                    "retrieval_status": retrieval.status.value,
                    "sources": [
                        ref.model_dump(mode="json") for ref in bundle.source_references
                    ]
                    if bundle
                    else [],
                    "role": "ai",
                    "conversation_id": session.conversation_id,
                },
                session,
            )
        except Exception as e:
            logger.exception("Error in respond_with_ai", extra={"error": e})
            await send_to_end_user(
                {
                    "type": "error",
                    "message": (
                        "Something went wrong while generating a reply. "
                        "Please try again in a moment, or ask to speak with an admissions counsellor."
                    ),
                    "role": "system",
                    "conversation_id": session.conversation_id,
                },
                session,
            )
