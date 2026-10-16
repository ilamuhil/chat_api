"""Deterministic lead-state merge; no LLM, database, or scheduling calls.

Supply SourceRecords from your persisted events, NEVER from the AI response.
Source checks establish provenance, not semantic truth of the extracted claim.
An optional claim_verifier can enforce application-specific semantic checks.
Persist the result with a compare-and-swap on the input revision. Do not replay
this service against an older database snapshot or reconstruct action outcomes
from model text. Follow-up creation/cancellation belongs to the action handler.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.agent.lead_capture_models import (
    Action,
    ActionSignal,
    ActionSignalType,
    ActionStatus,
    Attribution,
    ConflictRecord,
    ContractModel,
    CurrentRequest,
    FactValue,
    FieldEvidence,
    Intent,
    LeadCaptureState,
    LeadField,
    LeadTurnUpdate,
    NonBlank,
    QuestionRecord,
    QuestionResolution,
    SourceKind,
)


class SourceRecord(ContractModel):
    """Trusted event metadata loaded by the application, scoped to this chat."""

    source_id: NonBlank
    conversation_id: UUID
    source_kind: SourceKind
    observed_at: AwareDatetime


class MergePolicy(ContractModel):
    capture_leads: bool = True
    enabled_fields: set[LeadField] = Field(
        default_factory=lambda: set(LeadField) - {LeadField.STUDENT_GENDER}
    )


class MergeStatus(StrEnum):
    ACCEPTED = "accepted"
    UNCHANGED = "unchanged"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    DEFERRED = "deferred"


class MergeDecision(ContractModel):
    category: NonBlank
    status: MergeStatus
    reason: NonBlank
    field: LeadField | None = None
    previous_value: FactValue = None
    proposed_value: FactValue = None


class LeadMergeResult(ContractModel):
    state: LeadCaptureState
    decisions: list[MergeDecision] = Field(default_factory=list)
    new_conflicts: list[ConflictRecord] = Field(default_factory=list)
    action_signals_to_handle: list[ActionSignal] = Field(default_factory=list)
    pending_action_requires_review: bool = False
    state_changed: bool = False

    @property
    def accepted_changes(self) -> list[MergeDecision]:
        return [d for d in self.decisions if d.status == MergeStatus.ACCEPTED]

    @property
    def rejected_changes(self) -> list[MergeDecision]:
        return [d for d in self.decisions if d.status == MergeStatus.REJECTED]


# Return False if an otherwise well-formed extracted operation is unsupported.
# Receives None for explicit clears. Resolve text using the evidence source IDs.
ClaimVerifier = Callable[[LeadField, FactValue, list[FieldEvidence]], bool]
_IDENTITY_FIELDS = {
    LeadField.NAME,
    LeadField.EMAIL,
    LeadField.PHONE,
    LeadField.ENQUIRER_TYPE,
    LeadField.CONSENT_TO_CONTACT,
}
_CONTACT_ACTIONS = {
    Action.SCHEDULE_CALLBACK,
    Action.SEND_COURSE_DETAILS,
    Action.APPLICATION_FOLLOW_UP,
}


def _combine_evidence(
    old: list[FieldEvidence],
    new: list[FieldEvidence],
) -> list[FieldEvidence]:
    combined = []
    keys = set()
    for e in [*old, *new]:
        key = (e.source_id, e.source_kind, e.observed_at, e.attribution, e.uncertain)
        if key not in keys:
            keys.add(key)
            combined.append(e.model_copy(deep=True))
    return combined


def _latest(evidence: list[FieldEvidence]):
    return max((e.observed_at for e in evidence), default=None)


class LeadMergeService:
    def __init__(
        self,
        policy: MergePolicy | None = None,
        *,
        claim_verifier: ClaimVerifier | None = None,
    ) -> None:
        self.policy = (policy or MergePolicy()).model_copy(deep=True)
        self.claim_verifier = claim_verifier

    @staticmethod
    def _check_revision(state: LeadCaptureState, expected_revision: int | None) -> None:
        if expected_revision is not None and state.revision != expected_revision:
            raise ValueError(
                f"State revision mismatch: expected {expected_revision}, got {state.revision}"
            )

    @staticmethod
    def _evidence_error(
        evidence: list[FieldEvidence],
        state: LeadCaptureState,
        sources: Mapping[str, SourceRecord],
        *,
        allow_uncertain: bool = False,
        field: LeadField | None = None,
    ) -> str | None:
        if not evidence:
            return "No supporting evidence"
        for e in evidence:
            source = sources.get(e.source_id)
            if source is None or source.source_id != e.source_id:
                return "Source ID was not found in the trusted event records"
            if source.conversation_id != state.conversation_id:
                return "Evidence belongs to another conversation"
            if (
                source.source_kind != e.source_kind
                or source.observed_at != e.observed_at
            ):
                return "Evidence kind or timestamp disagrees with the persisted event"
            if e.uncertain and not allow_uncertain:
                return "Evidence is uncertain; clarification is required"
            if e.attribution in {Attribution.OTHER, Attribution.UNCLEAR}:
                return "Evidence is not clearly attributed to the enquirer or student"
            if field in _IDENTITY_FIELDS and e.attribution != Attribution.ENQUIRER_SELF:
                return "Enquirer identity/contact fields need enquirer attribution"
        return None

    def merge(
        self,
        state: LeadCaptureState,
        update: LeadTurnUpdate,
        *,
        sources: Mapping[str, SourceRecord],
        expected_revision: int | None = None,
    ) -> LeadMergeResult:
        """Return a new state; never mutate inputs or perform external actions.

        Revision increases once if persisted state changed, not for rejected or
        repeated proposals. Equal timestamps cannot establish correction order.
        Contact-consent changes use the newest explicit fact without requiring
        corrected_fields. Evidence-backed clears also do not require that flag.
        """
        self._check_revision(state, expected_revision)
        # Revalidate public, mutable model inputs before using them.
        original = LeadCaptureState.model_validate(state.model_dump())
        update = LeadTurnUpdate.model_validate(update.model_dump(exclude_unset=True))
        work = original.model_copy(deep=True)
        result = LeadMergeResult(state=work)

        def record(category, status, reason, field=None, previous=None, proposed=None):
            result.decisions.append(
                MergeDecision(
                    category=category,
                    status=status,
                    reason=reason,
                    field=field,
                    previous_value=previous,
                    proposed_value=proposed,
                )
            )

        for patch in update.patches:
            for field in sorted(patch.changed_fields):
                clearing = field in patch.clear_fields
                value = None if clearing else getattr(patch.set_values, field)
                old_value = getattr(work.profile, field)
                evidence = patch.evidence[field]
                old_evidence = work.field_evidence.get(field, [])
                error = self._evidence_error(evidence, work, sources, field=field)
                revoking_consent = (
                    field == LeadField.CONSENT_TO_CONTACT and value is False
                )
                if error is None and not clearing and not revoking_consent:
                    if (
                        not self.policy.capture_leads
                        or field not in self.policy.enabled_fields
                    ):
                        error = "Collection is disabled for this field"
                # Clearing existing data is permitted even if collection is disabled.
                if error is None and self.claim_verifier is not None:
                    if not self.claim_verifier(field, value, evidence):
                        error = "Application claim verification rejected the operation"
                if error:
                    record(
                        "profile", MergeStatus.REJECTED, error, field, old_value, value
                    )
                    continue

                incoming_time = _latest(evidence)
                existing_time = _latest(old_evidence)
                decline_time = _latest(work.declined_fields.get(field, []))
                if (
                    not clearing
                    and incoming_time is not None
                    and decline_time is not None
                    and incoming_time <= decline_time
                ):
                    record(
                        "profile",
                        MergeStatus.REJECTED,
                        "Proposal predates a refusal",
                        field,
                        old_value,
                        value,
                    )
                    continue
                if (
                    incoming_time is not None
                    and existing_time is not None
                    and incoming_time < existing_time
                ):
                    record(
                        "profile",
                        MergeStatus.REJECTED,
                        "Proposal is older than the accepted fact or clear",
                        field,
                        old_value,
                        value,
                    )
                    continue
                if (
                    value != old_value
                    and existing_time is not None
                    and incoming_time == existing_time
                ):
                    record(
                        "profile",
                        MergeStatus.REJECTED,
                        "Equal timestamps cannot establish replacement order",
                        field,
                        old_value,
                        value,
                    )
                    continue

                can_replace = (
                    clearing
                    or old_value is None
                    or value == old_value
                    or field in patch.corrected_fields
                    or field == LeadField.CONSENT_TO_CONTACT
                )
                if not can_replace:
                    conflict = ConflictRecord(
                        field=field,
                        accepted_value=old_value,
                        proposed_value=value,
                        accepted_evidence=old_evidence,
                        proposed_evidence=evidence,
                        reason="Different value supplied without an explicit correction",
                    )
                    if conflict not in work.unresolved_conflicts:
                        work.unresolved_conflicts.append(conflict)
                        result.new_conflicts.append(conflict.model_copy(deep=True))
                    record(
                        "profile",
                        MergeStatus.CONFLICT,
                        conflict.reason,
                        field,
                        old_value,
                        value,
                    )
                    continue

                setattr(work.profile, field, value)
                work.field_evidence[field] = (
                    _combine_evidence(old_evidence, evidence)
                    if value == old_value
                    else _combine_evidence([], evidence)
                )
                # A later voluntary answer reopens a previously declined field.
                if not clearing:
                    work.declined_fields.pop(field, None)
                if clearing or field in patch.corrected_fields:
                    kept_conflicts = []
                    for conflict in work.unresolved_conflicts:
                        proposed_time = _latest(conflict.proposed_evidence)
                        if conflict.field != field or (
                            proposed_time is not None
                            and incoming_time is not None
                            and proposed_time > incoming_time
                        ):
                            kept_conflicts.append(conflict)
                    work.unresolved_conflicts = kept_conflicts
                changed = (
                    value != old_value or work.field_evidence[field] != old_evidence
                )
                record(
                    "profile",
                    MergeStatus.ACCEPTED if changed else MergeStatus.UNCHANGED,
                    "Explicit clear" if clearing else "Accepted supported field value",
                    field,
                    old_value,
                    value,
                )

        for field, evidence in update.declined_fields.items():
            error = self._evidence_error(evidence, work, sources, field=field)
            known_time = _latest(work.field_evidence.get(field, []))
            prior_time = _latest(work.declined_fields.get(field, []))
            cutoff = max(
                (t for t in [known_time, prior_time] if t is not None), default=None
            )
            refusal_time = _latest(evidence)
            if (
                error is None
                and refusal_time is not None
                and cutoff is not None
                and refusal_time < cutoff
            ):
                error = "Refusal predates newer field information"
            if error:
                record("decline", MergeStatus.REJECTED, error, field)
                continue
            prior = work.declined_fields.get(field, [])
            combined = _combine_evidence(prior, evidence)
            work.declined_fields[field] = combined
            record(
                "decline",
                MergeStatus.ACCEPTED if combined != prior else MergeStatus.UNCHANGED,
                "Refusal recorded; this does not erase data or revoke contact consent",
                field,
            )

        if update.observed_intent is not None:
            error = self._evidence_error(update.intent_evidence, work, sources)
            old_request_time = (
                _latest(work.current_request.evidence) if work.current_request else None
            )
            intent_time = _latest(update.intent_evidence)
            if (
                error is None
                and intent_time is not None
                and old_request_time is not None
                and intent_time < old_request_time
            ):
                error = "Intent evidence is older than the current request"
            if error:
                record("intent", MergeStatus.REJECTED, error)
            elif update.observed_intent == Intent.UNKNOWN:
                record(
                    "intent",
                    MergeStatus.UNCHANGED,
                    "Unknown intent does not erase an existing request",
                )
            else:
                previous_request = work.current_request
                if (
                    previous_request is not None
                    and previous_request.intent == update.observed_intent
                ):
                    description = previous_request.description
                    evidence = _combine_evidence(
                        previous_request.evidence, update.intent_evidence
                    )
                else:
                    description = None
                    evidence = update.intent_evidence
                work.current_request = CurrentRequest(
                    intent=update.observed_intent,
                    description=description,
                    evidence=evidence,
                )
                record(
                    "intent",
                    MergeStatus.ACCEPTED,
                    "Current request updated; pending action preserved",
                )

        scope = update.course_scope_update
        if scope is not None:
            error = self._evidence_error(
                scope.evidence, work, sources, allow_uncertain=scope.ambiguous
            )
            old_time = _latest(work.active_course_scope.evidence)
            scope_time = _latest(scope.evidence)
            if (
                error is None
                and scope_time is not None
                and old_time is not None
                and scope_time < old_time
            ):
                error = "Course scope is older than the current scope"
            if error:
                record("scope", MergeStatus.REJECTED, error)
            else:
                work.active_course_scope = scope.model_copy(deep=True)
                record(
                    "scope",
                    MergeStatus.ACCEPTED,
                    "Discussion scope updated without changing course interest",
                )

        resolution = update.question_resolution
        question = work.pending_question
        if resolution is not None:
            if question is None or resolution == QuestionResolution.NOT_ADDRESSED:
                record(
                    "question", MergeStatus.UNCHANGED, "No pending question resolved"
                )
            else:
                error = self._evidence_error(
                    update.question_resolution_evidence, work, sources
                )
                resolution_time = _latest(update.question_resolution_evidence)
                if (
                    error is None
                    and resolution_time is not None
                    and resolution_time <= question.asked_at
                ):
                    error = "Resolution must follow the pending question"
                if error:
                    record("question", MergeStatus.REJECTED, error)
                elif resolution == QuestionResolution.UNCLEAR:
                    record(
                        "question",
                        MergeStatus.DEFERRED,
                        "Keep the pending question for clarification",
                    )
                else:
                    outstanding = []
                    for field in question.target_fields:
                        supplied_at = _latest(work.field_evidence.get(field, []))
                        declined_at = _latest(work.declined_fields.get(field, []))
                        supplied = (
                            getattr(work.profile, field) is not None
                            and supplied_at is not None
                            and supplied_at > question.asked_at
                        )
                        declined = (
                            declined_at is not None and declined_at > question.asked_at
                        )
                        if not supplied and not declined:
                            outstanding.append(field)
                    if outstanding:
                        work.pending_question = QuestionRecord(
                            **{**question.model_dump(), "target_fields": outstanding}
                        )
                        record(
                            "question",
                            MergeStatus.DEFERRED,
                            "Some requested fields remain unanswered",
                        )
                    else:
                        work.pending_question = None
                        record(
                            "question",
                            MergeStatus.ACCEPTED,
                            "Pending question resolved",
                        )

        for signal in update.action_signals:
            error = self._evidence_error(signal.evidence, work, sources)
            pending = work.pending_action
            if error is None and signal.request_id is not None:
                if (
                    pending is None
                    or signal.request_id != pending.request_id
                    or signal.action_type != pending.action_type
                ):
                    error = "Signal does not match the outstanding action"
            if error is None and signal.signal_type != ActionSignalType.REQUESTED:
                if pending is None or signal.action_type != pending.action_type:
                    error = "No matching pending action to modify or cancel"
                else:
                    signal_time = _latest(signal.evidence)
                    request_time = _latest(pending.evidence)
                    if (
                        signal_time is not None
                        and request_time is not None
                        and signal_time <= request_time
                    ):
                        error = (
                            "Action modification/cancellation must follow the request"
                        )
            if error:
                record("action", MergeStatus.REJECTED, error)
                continue
            if signal not in result.action_signals_to_handle:
                result.action_signals_to_handle.append(signal.model_copy(deep=True))
            record(
                "action",
                MergeStatus.DEFERRED,
                "Validated signal returned to action handler; no action executed",
            )

        pending = work.pending_action
        result.pending_action_requires_review = bool(
            pending is not None
            and pending.action_type in _CONTACT_ACTIONS
            and pending.status not in {ActionStatus.COMPLETED, ActionStatus.CANCELLED}
            and work.profile.consent_to_contact is False
        )
        result.state_changed = work.model_dump() != original.model_dump()
        if result.state_changed:
            work.revision += 1
        result.state = LeadCaptureState.model_validate(work.model_dump())
        return result

    def record_question(
        self,
        state: LeadCaptureState,
        question: QuestionRecord,
        *,
        expected_revision: int | None = None,
    ) -> LeadCaptureState:
        """Call after persisting the actual assistant question; no AI-generated IDs."""
        self._check_revision(state, expected_revision)
        work = LeadCaptureState.model_validate(state.model_dump())
        question = QuestionRecord.model_validate(question.model_dump())
        for field in question.target_fields:
            if not self.policy.capture_leads or field not in self.policy.enabled_fields:
                raise ValueError(f"Collection is disabled for {field}")
            if field in work.declined_fields:
                raise ValueError(f"Do not repeat a declined question for {field}")
        existing = [q for questions in work.asked_fields.values() for q in questions]
        if work.pending_question is not None:
            existing.append(work.pending_question)
        if any(q.message_id == question.message_id for q in existing):
            if not any(q == question for q in existing):
                raise ValueError(
                    "Question message ID was reused with different contents"
                )
            return work
        if work.pending_question is not None:
            raise ValueError(
                "Resolve the existing pending question before recording another"
            )
        work.pending_question = question.model_copy(deep=True)
        for field in set(question.target_fields):
            work.asked_fields.setdefault(field, []).append(
                question.model_copy(deep=True)
            )
        work.revision += 1
        return LeadCaptureState.model_validate(work.model_dump())
