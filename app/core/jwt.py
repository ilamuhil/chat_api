from __future__ import annotations

from pathlib import Path
from typing import Any

import jwt


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


PUBLIC_KEY = _load_public_key()


def verify_token(token: str, options: dict[str, Any]) -> dict[str, Any] | None:
    try:
        if not PUBLIC_KEY:
            raise RuntimeError("public.pem not found or empty")
        claims = jwt.decode(
            token,
            PUBLIC_KEY,
            algorithms=["RS256"],
            options=options,
            audience="chat-server",
            issuer="next-server",
        )
        return claims
    except jwt.ExpiredSignatureError as e:
        print(f"Token expired: {e}")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Invalid token: {e}")
        return None
    except Exception as e:
        print(f"Error verifying token: {e}")
        return None


