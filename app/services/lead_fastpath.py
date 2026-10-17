"""Closed English FAQ grammar: skip extraction only when no visitor facts/actions fit.

Unknown wording deliberately falls back to the extractor. This is not a general
intent classifier and must never infer consent or personal course interest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.lead_capture_models import Intent, LeadCaptureState


@dataclass(frozen=True)
class FactualTurn:
    intent: Intent
    course_names: tuple[str, ...] = ()


# Subject spans may be course titles/codes, but cannot contain clauses or facts.
_SUBJECT = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[ -][A-Za-z][A-Za-z0-9]*){0,7}")
_UNSAFE_SUBJECT = re.compile(
    r"\b(?:i|my|me|mine|we|our|us|your|his|her|their|he|she|they|it|this|that|these|those|"
    r"and|or|but|also|because|after|before|if|when|with|without|for|from|to|in|at|"
    r"am|is|are|have|has|want|need|prefer|choose|selected|interested|completed|graduated|"
    r"born|age|student|daughter|son|parent|guardian|name|email|phone|number|contact|"
    r"call|send|book|schedule|arrange|cancel|change|correct|update|remove|delete|forget|"
    r"consent|permission|yes|no|not|never|stop|please|instead|actually|tomorrow|today)\b",
    re.I,
)
_PATTERNS = (
    (r"what courses do you offer", Intent.COURSE_INFO),
    (
        r"(?:what|which) (?:courses|programs|programmes) are available",
        Intent.COURSE_INFO,
    ),
    (r"how (?:do|can) i apply", Intent.APPLICATION),
    (
        r"what (?:is|are) (?:the )?(?:fees?|cost|price)(?: for| of) (?P<subject>.+)",
        Intent.FEES,
    ),
    (
        r"what (?:is|are) (?:the )?duration(?: for| of) (?P<subject>.+)",
        Intent.COURSE_INFO,
    ),
    (
        r"what (?:is|are) (?:the )?(?:eligibility requirements|eligibility criteria|entry requirements)(?: for| of) (?P<subject>.+)",
        Intent.ELIGIBILITY,
    ),
)
_FOLLOWUPS = (
    (r"(?:and )?(?:the )?(?:fees?|cost|price)", Intent.FEES),
    (r"(?:and )?(?:the )?duration", Intent.COURSE_INFO),
    (r"is it online or offline", Intent.BATCH_OR_SCHEDULE),
    (
        r"(?:and )?does (?:it|that course) include (?:placement assistance|mentoring|a certificate)",
        Intent.COURSE_INFO,
    ),
)


def detect_factual_turn(message: str, state: LeadCaptureState) -> FactualTurn | None:
    # Even an apparent FAQ can answer a pending question or modify an action.
    if state.pending_question or state.pending_action or state.unresolved_conflicts:
        return None
    if not message.isascii() or len(message) > 180:
        return None
    text = " ".join(message.strip().split())
    if text.endswith("?"):
        text = text[:-1].rstrip()
    for pattern, intent in _PATTERNS:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            subject = match.groupdict().get("subject")
            if subject is None:
                return FactualTurn(intent)
            # A quoted/compound/ambiguous subject uses the regular extractor.
            if not _SUBJECT.fullmatch(subject) or _UNSAFE_SUBJECT.search(subject):
                return None
            return FactualTurn(intent, (subject,))
    scope = state.active_course_scope
    if len(scope.course_names) == 1 and not scope.ambiguous:
        for pattern, intent in _FOLLOWUPS:
            if re.fullmatch(pattern, text, re.I):
                return FactualTurn(intent)  # Preserve the existing discussion scope.
    return None
