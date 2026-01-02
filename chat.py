import os
from typing import Any

from fastapi import WebSocket

from util import ChatSession


async def _send_json_safe(socket: WebSocket | None, data: dict[str, Any]) -> None:
  """Send JSON if the socket exists; otherwise do nothing."""
  if socket is None:
    return
  await socket.send_json(data)


async def send_to_support_agent(message_data: dict[str, Any], session: ChatSession) -> None:
  await _send_json_safe(session.agent_socket, message_data)

async def send_to_end_user(message_data: dict[str, Any], session: ChatSession) -> None:
  await _send_json_safe(session.user_socket, message_data)

async def respond_with_ai(message_data: dict[str, Any], session: ChatSession) -> None:
  # Let the client show a typing indicator while we generate.
  await _send_json_safe(
    session.user_socket,
    {"type": "typing", "from": "assistant", "is_typing": True, "conversation_id": session.conversation_id},
  )

  try:
    # Ensure env is loaded for the current worker process (reloaders/dotfiles can be tricky).
    from env_loader import load_app_env
    load_app_env()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
      raise RuntimeError("OPENAI_API_KEY is not set")
    api_key = api_key.strip()

    # Import lazily so the server can start even if OpenAI isn't installed yet.
    from openai import AsyncOpenAI  # type: ignore

    client = AsyncOpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    user_text = ""
    if isinstance(message_data, dict):
      user_text = str(message_data.get("message") or message_data.get("content") or "")

    resp = await client.chat.completions.create(
      model=model,
      messages=[
        {"role": "system", "content": "You are a helpful customer support assistant."},
        {"role": "user", "content": user_text},
      ],
    )
    answer = (resp.choices[0].message.content or "").strip()

    await _send_json_safe(
      session.user_socket,
      {
        "type": "message",
        "message": answer,
        "role": "assistant",
        "conversation_id": session.conversation_id,
      },
    )
  except Exception as e:
    await _send_json_safe(
      session.user_socket,
      {
        "type": "error",
        "message": f"AI error: {e}",
        "conversation_id": session.conversation_id,
      },
    )
  finally:
    await _send_json_safe(
      session.user_socket,
      {"type": "typing", "from": "assistant", "is_typing": False, "conversation_id": session.conversation_id},
    )
