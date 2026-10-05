from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.rag.shared_dataclasses import RetrievalResult


@dataclass
class InstituteContext:
    bot_prefs: dict[str, Any]
    retrieval_result: RetrievalResult | None = None
    conversation_id: uuid.UUID | None = None
