from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.jwt import verify_token


PUBLIC_PATHS = {"/docs", "/openapi.json", "/redoc", "/api/health"}


async def verify_jwt_middleware(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        claims = verify_token(token, {"require": ["exp", "iat", "aud", "iss", "organization_id"]})
        if claims is None:
            return JSONResponse(content={"error": "Invalid authorization header"}, status_code=401)
        request.state.organization_id = claims.get("organization_id")
        request.state.claims = claims
        return await call_next(request)

    return JSONResponse(content={"error": "Missing authorization header"}, status_code=401)


