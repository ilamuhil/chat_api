"""Conservative local signals for whether a search query needs conversation context."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.shared_dataclasses import ConversationTurn


@dataclass(frozen=True)
class RewriteDecision:
    needs_rewrite: bool
    reason: str


_REFERENCE = re.compile(
    r"\b(?:it|its|they|them|their|former|latter|above|previous)\b"
    r"|\b(?:this|that|these|those|same|other)\s+(?:one|ones|course|courses|program|programs|option|options|batch|batches)\b"
    r"|\b(?:compare|versus|vs)\s+(?:this|that|these|those)\b",
    re.I,
)
_CORRECTION = re.compile(r"^(?:no[, ]|actually\b|i meant\b|instead\b|not that\b)", re.I)
_CODE = re.compile(
    r"\b(?=[a-z0-9_-]*[a-z])(?=[a-z0-9_-]*\d)[a-z][a-z0-9_-]{2,}\b", re.I
)
_ASPECT = re.compile(
    r"\b(?:fees?|cost|price|duration|eligibility|requirements?|schedule|timing|scholarships?|placements?|certificate|curriculum|syllabus|installments?|instalments?)\b",
    re.I,
)
_SUBJECT = re.compile(r"\b(?:for|about|on)\s+(?:the\s+)?([\w][\w '&-]*)", re.I)
_GENERIC = re.compile(
    r"\b(?:what|which|list|tell me|do you|does your|how many)\b.*\b(?:courses|programs|programmes)\b"
    r"|\b(?:how (?:do|can|should) i (?:apply|register|enrol|enroll)|application process|admission process)\b"
    r"|\b(?:where|address|location|contact|working hours)\b.*\b(?:campus|institute|you|office)\b",
    re.I,
)
_COMPLETE = re.compile(
    r"^(?:and\s+)?(?:what|which|how|when|where|is|are|does|do|can|tell me|explain|list)\b",
    re.I,
)
_SOCIAL = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|okay|ok|yes|no|good morning|good evening)[.! ]*$",
    re.I,
)
_PERSONAL = re.compile(
    r"\b(?:my name is|my (?:email|phone|number) is|call me|do not contact me|don.t contact me)\b|\b[^\s@]+@[^\s@]+\.[^\s@]+\b",
    re.I,
)


def detect_rewrite_need(
    message: str, history: Sequence[ConversationTurn]
) -> RewriteDecision:
    text = " ".join(message.replace("’", "'").strip().split())
    if not text:
        return RewriteDecision(False, "empty_message")
    if not any(turn.content.strip() for turn in history):
        return RewriteDecision(False, "no_history")
    if _REFERENCE.search(text):
        return RewriteDecision(True, "unresolved_reference")
    if _SOCIAL.fullmatch(text) or (_PERSONAL.search(text) and not _ASPECT.search(text)):
        return RewriteDecision(False, "conversation_or_personal_details")
    if _CORRECTION.search(text):
        return RewriteDecision(True, "contextual_correction")
    if not text.isascii():
        return RewriteDecision(True, "language_uncertain")
    if _CODE.search(text):
        return RewriteDecision(False, "explicit_subject")
    subject = _SUBJECT.search(text)
    if subject and _COMPLETE.search(text):
        words = re.findall(r"[a-z]+", subject.group(1).casefold())
        excluded = {
            "me",
            "us",
            "you",
            "your",
            "this",
            "that",
            "these",
            "those",
            "one",
            "ones",
            "course",
            "program",
            "option",
            "fees",
            "fee",
            "duration",
            "eligibility",
            "please",
        }
        if len(words) >= 2 and all(word not in excluded for word in words):
            return RewriteDecision(False, "explicit_subject_phrase")
    if _GENERIC.search(text) and not _ASPECT.search(text):
        return RewriteDecision(False, "institute_question")
    if _ASPECT.search(text) or re.match(
        r"^(?:and|also|what about|how about|online or offline)\b", text, re.I
    ):
        return RewriteDecision(True, "missing_subject_or_ellipsis")
    return RewriteDecision(True, "context_uncertain")
