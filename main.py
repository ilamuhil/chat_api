# Load env early (before importing modules that may read environment variables)
from env_loader import load_app_env

load_app_env()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from auth import authenticate_socket
from chat import send_to_support_agent, send_to_end_user, respond_with_ai
from util import ChatSession
app = FastAPI()


ACTIVE_SESSIONS : dict[str,ChatSession] = {}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)





@app.websocket("/api/chat/ws")
async def chat(websocket:WebSocket):
    await websocket.accept()
    session = await authenticate_socket(websocket,ACTIVE_SESSIONS)
    if session is None:
        return
    try:
        while True:
            message_data = await websocket.receive_json()
            msg_type = None
            if isinstance(message_data, dict):
                msg_type = message_data.get("type")
            
            # Condition -> when the end user is sending a message
            if websocket == session.user_socket:
                # Typing indicator: forward to agent only in human mode
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
                # Send message to live support agent
                if session.mode == "human":
                    await send_to_support_agent(message_data,session)
                    
                # Send message to end user with AI response   
                elif session.mode == "ai":
                    await respond_with_ai(message_data,session)
            
            # Condition -> when the live support agent is sending a message
            elif websocket == session.agent_socket:
                # Typing indicator: forward to end user
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
                # Send message to end user
                await send_to_end_user(message_data,session)
                
    except WebSocketDisconnect:
        # Disconnect the user or agent from the session
        if websocket == session.user_socket:
            session.user_disconnect()
        elif websocket == session.agent_socket:
            session.agent_disconnect()
        print("Client disconnected")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await websocket.close()
        
        