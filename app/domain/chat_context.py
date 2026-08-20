from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class InstituteContext:
    bot_prefs: dict[str, Any]
    rag_context: str = ""
    conversation_id: uuid.UUID | None = None
