from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.chat import ChatSession
from app.services.chat import respond_with_ai, send_to_end_user, send_to_support_agent
from app.ws.auth import authenticate_socket


router = APIRouter()

# In-memory map; swap for Redis later if you scale horizontally.
ACTIVE_SESSIONS: dict[str, ChatSession] = {}


@router.websocket("/api/chat/ws")
async def chat(websocket: WebSocket):
    await websocket.accept()
    session = await authenticate_socket(websocket, ACTIVE_SESSIONS)
    if session is None:
        return

    try:
        while True:
            message_data = await websocket.receive_json()
            msg_type = message_data.get("type") if isinstance(message_data, dict) else None

            # end user -> agent/ai
            if websocket == session.user_socket:
                if msg_type == "typing":
                    await send_to_support_agent(
                        {
                            **message_data,
                            "type": "typing",
                            "from": "user",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                if session.mode == "human":
                    await send_to_support_agent(message_data, session)
                elif session.mode == "ai":
                    await respond_with_ai(message_data, session)

            # agent -> user
            elif websocket == session.agent_socket:
                if msg_type == "typing":
                    await send_to_end_user(
                        {
                            **message_data,
                            "type": "typing",
                            "from": "agent",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue
                await send_to_end_user(message_data, session)

    except WebSocketDisconnect:
        if websocket == session.user_socket:
            session.user_disconnect()
        elif websocket == session.agent_socket:
            session.agent_disconnect()
        print("Client disconnected")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await websocket.close()


