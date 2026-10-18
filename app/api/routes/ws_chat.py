from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from time import perf_counter
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.websockets import WebSocketState

from app.db.session import create_async_chat_db_session
from app.models.chat_db_models import Messages
from app.services.chat import (
    log_message,
    respond_with_ai,
    send_to_end_user,
    send_to_support_agent,
    send_typing_to_end_user,
    send_typing_to_support_agent,
)
from app.services.latency import (
    TurnLatency,
    current_latency,
    latency_stage,
    persist_latency,
    track_turn,
)
from app.ws.agent_tool_api import get_conversation
from app.ws.auth import authenticate_socket
from app.ws.handlers import (
    associate_lead_with_conversation,
    end_chat_session,
    handle_form_capture,
    has_lead,
    load_bot_prefs,
    request_end_chat,
    send_first_message_once,
)
from app.ws.handover import (
    AITurnScope,
    current_ai_scope,
    refresh_session,
    relay_to,
    state_payload,
    touch_connection,
    unregister_connection,
)
from app.ws.registry import ACTIVE_SESSIONS

logger = logging.getLogger(__name__)


router = APIRouter()

# Socket objects stay local; persisted ownership and Redis route between processes.


async def process_ai_queue(ai_queue, session, bot_pref, agent) -> None:
    while True:
        queued_message, timing, scope = await ai_queue.get()
        token = current_ai_scope.set(scope)

        async def run_turn(
            message=queued_message, turn_timing=timing, turn_scope=scope
        ):
            async with track_turn(turn_timing, session, bot_pref):
                # Drop queued turns invalidated by takeover or a reconnect.
                await refresh_session(session)
                if (
                    session.mode != "ai"
                    or session.handover_version != turn_scope.version
                ):
                    turn_timing.outcome = "cancelled"
                    return
                await respond_with_ai(
                    message,
                    bot_pref,
                    session,
                    agent,
                )

        task = asyncio.create_task(run_turn())
        session.ai_turn_task = task
        current_ai_scope.reset(token)
        try:
            await task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            timing.outcome = "cancelled"
        except Exception:
            logger.exception(
                "Error processing queued AI message",
                extra={"conversation_id": session.conversation_id},
            )
        finally:
            if session.ai_turn_task is task:
                session.ai_turn_task = None
            ai_queue.task_done()


@router.websocket("/chat/ws")
async def chat(websocket: WebSocket):
    await websocket.accept()
    try:
        metadata = await authenticate_socket(websocket, ACTIVE_SESSIONS)
    except WebSocketDisconnect:
        return
    if metadata is None:
        return

    session, bot_id = metadata
    role = websocket.state.chat_role
    connection_id = websocket.state.connection_id

    ai_queue: asyncio.Queue[tuple[dict[str, Any], TurnLatency, AITurnScope]] | None = (
        None
    )
    ai_worker_task: asyncio.Task[None] | None = None

    bot_pref = {}
    try:
        if role == "user":
            await websocket.send_json(
                {
                    "type": "connection_authenticated",
                    "conversation_id": session.conversation_id,
                }
            )
        bot_pref = (
            await load_bot_prefs(websocket, bot_id)
            if role == "user"
            else {"bot_id": str(bot_id)}
        )
        if bot_pref is None:
            return

        first_message = bot_pref.get("first_message") or "Hi, How can I help you today?"
        greeting_key = f"greeting_sent:{session.conversation_id}"
        conversation_uuid = uuid.UUID(str(session.conversation_id))
        logger.info("Conversation UUID", extra={"conversation_uuid": conversation_uuid})

        visitor_uuid = None
        lead_exists, lead_id = False, None
        if role == "user":
            try:
                visitor_uuid = uuid.UUID(str(session.visitor_id))
            except (ValueError, TypeError):
                await websocket.close(code=1008, reason="Invalid visitor identity")
                return
            lead_exists, lead_id = await has_lead(
                visitor_uuid, bot_id, session.organization_id
            )
        form_required = websocket == session.user_socket and not lead_exists
        if websocket == session.user_socket:
            ai_queue = asyncio.Queue(maxsize=3)

            ai_worker_task = asyncio.create_task(
                process_ai_queue(ai_queue, session, bot_pref, websocket.app.state.agent)
            )

        await refresh_session(session)
        if role == "support_agent":
            await websocket.send_json(await state_payload(session))
            # Reconcile messages saved while this counsellor was disconnected.
            async with create_async_chat_db_session() as db:
                history = list(
                    (
                        await db.scalars(
                            select(Messages)
                            .where(Messages.conversation_id == conversation_uuid)
                            .order_by(Messages.created_at.desc(), Messages.id.desc())
                            .limit(50)
                        )
                    ).all()
                )
            await websocket.send_json(
                {
                    "type": "conversation_history",
                    "conversation_id": session.conversation_id,
                    "messages": [
                        {
                            "id": str(item.id),
                            "conversation_id": session.conversation_id,
                            "created_at": item.created_at.isoformat()
                            if item.created_at
                            else None,
                            "agent_id": str(item.agent_id) if item.agent_id else "",
                            "content_type": item.content_type,
                            "content": item.content,
                            "role": item.role,
                        }
                        for item in reversed(history)
                    ],
                }
            )
            await websocket.send_json(
                {"type": "agent_ready", "conversation_id": session.conversation_id}
            )
        if websocket == session.user_socket:
            await websocket.send_json(
                {
                    "type": "conversation_ready",
                    "conversation_id": session.conversation_id,
                    "form_required": form_required,
                }
            )
            await websocket.send_json(await state_payload(session))
        # Returning users already submitted the form. Skip it and only send the
        # greeting if this conversation has never received one.
        if websocket == session.user_socket and not form_required:
            # Returning visitor already has a lead; attach it to this conversation.
            if lead_exists and lead_id is not None:
                await associate_lead_with_conversation(conversation_uuid, lead_id)
            if session.mode == "ai":
                await send_first_message_once(
                    greeting_key,
                    conversation_uuid,
                    first_message,
                    session,
                )

        while True:
            # if conversation is closed disconnect the websocket
            try:
                conversation = await get_conversation(
                    conversation_uuid, open_only=False
                )
                if conversation is not None and conversation.status == "closed":
                    await end_chat_session(
                        session,
                        closed_by=conversation.closed_by or "system",
                        already_closed=True,
                    )
                    break
            except Exception:
                logger.exception(
                    "Could not find an open conversation",
                    extra={"conversation_id": conversation_uuid},
                )
                break
            message_data = await websocket.receive_json()
            received_at = perf_counter()
            await touch_connection(connection_id)
            conversation = await refresh_session(
                session,
                notify=isinstance(message_data, dict)
                and message_data.get("type") == "ping",
            )
            active_id = (
                session.active_user_connection_id
                if role == "user"
                else session.active_agent_connection_id
            )
            if active_id != connection_id or conversation.status != "open":
                break
            if not isinstance(message_data, dict):
                continue
            msg_type = (
                message_data.get("type") if isinstance(message_data, dict) else None
            )
            if msg_type is None and (
                "message" in message_data or "content" in message_data
            ):
                msg_type = "message"
                message_data["type"] = msg_type
            allowed_types = {"message", "typing", "file", "end_chat", "ping"}
            allowed_types |= (
                {"form_capture", "request_counsellor"}
                if role == "user"
                else {"agent_joined"}
            )
            if msg_type not in allowed_types:
                await websocket.send_json(
                    {"type": "error", "message": "Unsupported message type"}
                )
                continue
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            elif msg_type == "end_chat":
                try:
                    if await request_end_chat(session, websocket):
                        break
                except Exception:
                    logger.exception(
                        "Could not process end-chat request",
                        extra={"conversation_id": session.conversation_id},
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Could not process the end-chat request. Please try again.",
                        }
                    )
                continue

            logger.debug("WebSocket message received", extra={"type": msg_type})
            if msg_type == "request_counsellor" and websocket == session.user_socket:
                from app.agent.edu_agent import request_handover

                result = await request_handover(conversation_uuid)
                await websocket.send_json(
                    {
                        "type": "message",
                        "role": "system",
                        "message": result["message"],
                        "conversation_id": session.conversation_id,
                    }
                )
                continue
            if msg_type == "form_capture":
                if message_data.get("fallback") == "contact":
                    form_required = True
                if websocket != session.user_socket or visitor_uuid is None:
                    continue
                form_required = await handle_form_capture(
                    message_data,
                    form_required=form_required,
                    bot_id=bot_id,
                    session=session,
                    conversation_uuid=conversation_uuid,
                    greeting_key=greeting_key,
                    first_message=first_message,
                    visitor_uuid=visitor_uuid,
                )
                continue

            # end user -> agent/ai
            if websocket == session.user_socket:
                if form_required:
                    await send_to_end_user(
                        {
                            "type": "form_required",
                            "message": "Please submit your details before starting the conversation.",
                            "role": "system",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                if msg_type == "typing":
                    is_typing = bool(message_data.get("is_typing", False))
                    await send_typing_to_support_agent(session, is_typing, actor="user")
                    continue

                elif msg_type == "file":
                    file_key = message_data.get("message") or message_data.get(
                        "content"
                    )
                    if file_key:
                        await log_message(
                            session.conversation_id,
                            "user",
                            str(file_key),
                            "file",
                        )
                    if session.mode == "human":
                        await send_to_support_agent(
                            {
                                **message_data,
                                "role": "user",
                                "conversation_id": session.conversation_id,
                                "content_type": message_data.get("mime_type", "file"),
                                "file_url": message_data.get("file_url"),
                            },
                            session,
                        )
                    await send_to_end_user(
                        {
                            "type": "message",
                            "message": "File has been uploaded, a support agent will review it and get back to you soon. If there is anything else I can help you with let me know.",
                            "role": "system",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                user_content = message_data.get("message") or message_data.get(
                    "content"
                )
                timing = TurnLatency(received_at=received_at)
                if user_content:
                    token = current_latency.set(timing)
                    try:
                        with latency_stage("user_message_persistence"):
                            message_id = await log_message(
                                session.conversation_id,
                                "user",
                                str(user_content),
                                "text",
                            )
                    finally:
                        current_latency.reset(token)
                    if message_id is not None:
                        message_data["message_id"] = str(message_id)
                        message_data["id"] = str(message_id)
                        timing.request_message_id = message_id

                if session.mode == "human":
                    await send_typing_to_support_agent(session, False)
                    delivered = await send_to_support_agent(message_data, session)
                    if not delivered:
                        await websocket.send_json(
                            {
                                "type": "message",
                                "role": "system",
                                "message": "Your message was saved. The counsellor is reconnecting.",
                                "conversation_id": session.conversation_id,
                            }
                        )
                elif session.mode == "ai":
                    if ai_queue is not None:
                        if ai_queue.full():
                            await websocket.send_json(
                                {
                                    "type": "ai_unavailable",
                                    "code": "temporarily_busy",
                                    "message": "Please wait for your earlier messages to finish, then try again.",
                                    "fallback": "retry",
                                    "conversation_id": session.conversation_id,
                                }
                            )
                        else:
                            timing.enqueued_at = perf_counter()
                            scope = AITurnScope(
                                session.conversation_id,
                                session.organization_id,
                                session.handover_version,
                                session.user_connection_id,
                            )
                            ai_queue.put_nowait((message_data, timing, scope))

            # agent -> user
            elif websocket == session.agent_socket:
                if msg_type == "typing":
                    is_typing = bool(message_data.get("is_typing", False))
                    await send_typing_to_end_user(
                        session, is_typing, actor="support_agent"
                    )
                    continue
                # Clear any lingering agent typing indicator before the message.
                await send_typing_to_end_user(session, False)
                agent_message = {
                    **message_data,
                    "role": "support_agent",
                    "conversation_id": session.conversation_id,
                }
                message_id = await log_message(
                    session.conversation_id,
                    "support_agent",
                    str(
                        agent_message.get("message")
                        or agent_message.get("content")
                        or ""
                    ),
                    "file" if msg_type == "file" else "text",
                )
                if message_id:
                    agent_message["id"] = str(message_id)
                delivered = await relay_to(
                    session,
                    "user",
                    agent_message,
                    sender_agent_connection_id=connection_id,
                )
                if not delivered:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Message saved. The visitor is reconnecting.",
                        }
                    )

    except WebSocketDisconnect as e:
        logger.info("Chat session was disconnected", extra={"error": str(e)})
    except RuntimeError as e:
        if websocket.application_state == WebSocketState.DISCONNECTED:
            logger.debug("Socket closed during chat setup or delivery")
        else:
            logger.exception("Error in chat session", extra={"error": e})
    except Exception as e:
        logger.exception("Error in chat session", extra={"error": e})
    finally:
        try:
            await unregister_connection(session, role, connection_id)
        except Exception:
            logger.exception("Could not detach disconnected socket")
        if role == "user" and session.user_connection_id == connection_id:
            session.user_disconnect()
        elif role == "support_agent" and session.agent_connection_id == connection_id:
            session.agent_disconnect()
        if (
            session.user_socket is None
            and session.agent_socket is None
            and ACTIVE_SESSIONS.get(session.conversation_id) is session
        ):
            ACTIVE_SESSIONS.pop(session.conversation_id, None)
        if ai_worker_task is not None:
            ai_worker_task.cancel()
            try:
                await ai_worker_task
            except asyncio.CancelledError:
                pass
        if ai_queue is not None:
            while not ai_queue.empty():
                _, timing, _ = ai_queue.get_nowait()
                timing.outcome = "cancelled"
                await persist_latency(timing, session, bot_pref)
                ai_queue.task_done()
        if (
            websocket.application_state != WebSocketState.DISCONNECTED
            and websocket.client_state != WebSocketState.DISCONNECTED
        ):
            with suppress(WebSocketDisconnect):
                await websocket.close()
