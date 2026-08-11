from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jwt
from jwt.types import Options

logger = logging.getLogger(__name__)


def _load_public_key() -> bytes | None:
    """
    Best-effort load of `public.pem`.
    Tries project root first, then CWD, so local runs behave as expected.
    """
    candidates = []
    try:
        root = Path(__file__).resolve().parents[2]
        candidates.append(root / "public.pem")
    except Exception:
        pass
    candidates.append(Path("public.pem"))

    for p in candidates:
        try:
            with p.open("rb") as f:
                key = f.read()
            if key:
                return key
        except Exception:
            continue
    return None


_UNSET = object()
_PUBLIC_KEY: Any = _UNSET


def get_public_key() -> bytes | None:
    global _PUBLIC_KEY
    # Reload if unset or previously failed (e.g. file not ready at first import).
    if _PUBLIC_KEY is _UNSET or _PUBLIC_KEY is None:
        _PUBLIC_KEY = _load_public_key()
    return _PUBLIC_KEY


def verify_token(token: str, options: Options | None = None) -> dict[str, Any] | None:
    try:
        public_key = get_public_key()
        if not public_key:
            raise RuntimeError("public.pem not found or empty")
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options=options,
            audience="chat-server",
            issuer="next-server",
        )
        return claims
    except jwt.ExpiredSignatureError as e:
        logger.warning("Token expired", extra={"error": str(e)})
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(
            "Invalid token",
            extra={"error": str(e), "error_type": type(e).__name__},
        )
        return None
    except Exception as e:
        logger.exception("Error verifying token", extra={"error": str(e)})
        return None


