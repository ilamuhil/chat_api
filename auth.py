import jwt
from util import ChatSession
from fastapi import WebSocket

try:
  with open("public.pem","rb") as f:
    PUBLIC_KEY = f.read()
    if PUBLIC_KEY is None:
      raise Exception("Public key is None")
except Exception as e:
  print(f"Error reading public key: {e}")
  PUBLIC_KEY = None


def verify_token(token:str) -> dict | None:
  try:
    payload = jwt.decode(token,PUBLIC_KEY,algorithms=["RS256"],options={"require":["exp","iat","aud","iss","conversation_id","organization_id","type"]},audience="chat-server",issuer="next-server")
    return payload
  except jwt.ExpiredSignatureError as e:
    print(f"Token expired: {e}")
    return None
  except jwt.InvalidTokenError as e:
    print(f"Invalid token: {e}")
    return None
  except Exception as e:
    print(f"Error verifying token: {e}")
    return None




async def authenticate_socket(websocket:WebSocket,active_sessions:dict[str,ChatSession]) -> ChatSession | None:
  data = await websocket.receive_json()
  if data is None:
    await websocket.close(code=1008,reason="No data provided")
    return None
  token = data.get("token")
  conversation_id = data.get("conversation_id")
  
  if token is None or conversation_id is None:
    await websocket.close(code=1008,reason="No token/conversation id provided")
    return None
  
  claims = verify_token(token)
  if claims is None:
    await websocket.close(code=1008,reason="Invalid token")
    return None
  
  # check if conversation id matches the token
  if claims.get("conversation_id") != conversation_id:
    await websocket.close(code=1008,reason="Conversation mismatch")
    return None
  
  # if end user connected to the socket
  if claims.get("type") == "user":
    session = ChatSession(conversation_id=conversation_id,organization_id=claims.get("organization_id"),user_socket=websocket)
    active_sessions[conversation_id] = session
    return session
  
  # if agent connected to the socket
  elif claims.get("type") == "agent":
    # connect to existing ChatSession
    session = active_sessions.get(conversation_id)
    if session is None:
      await websocket.close(code=1008,reason="Session not found")
      return None
    
    if session.organization_id != claims.get("organization_id"):
      await websocket.close(code=1008,reason="Organization mismatch")
      return None
    session.agent_connect(websocket)
    return session
  
  
  else:
    await websocket.close(code=1008,reason="Invalid type")
    return None
  
  
  