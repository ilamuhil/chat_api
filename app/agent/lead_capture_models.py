import re
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Practical HTML-style syntax check, not mailbox ownership/deliverability validation.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
# Preserve local numbers; do not infer a country code. Not full E.164 validation.
_PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
NonBlank = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
FactValue = str | int | bool | None


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnquirerType(StrEnum):
    STUDENT = "student"
    PARENT = "parent"
    GUARDIAN = "guardian"
    OTHER = "other"


class StudentGender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class PreferredMode(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    HYBRID = "hybrid"


class JoiningTimeline(StrEnum):
    AS_SOON_AS_POSSIBLE = "as_soon_as_possible"
    NEXT_AVAILABLE_INTAKE = "next_available_intake"
    NEXT_ACADEMIC_YEAR = "next_academic_year"
    SPECIFIC_PERIOD = "specific_period"
    EXPLORING = "exploring"


class LeadField(StrEnum):
    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    ENQUIRER_TYPE = "enquirer_type"
    STUDENT_NAME = "student_name"
    STUDENT_AGE = "student_age"
    STUDENT_GENDER = "student_gender"
    COURSE_INTEREST = "course_interest"
    EDUCATION_LEVEL = "education_level"
    PREFERRED_MODE = "preferred_mode"
    JOINING_TIMELINE = "joining_timeline"
    JOINING_PREFERENCE_DETAILS = "joining_preference_details"
    CONSENT_TO_CONTACT = "consent_to_contact"


class LeadProfile(ContractModel):
    name: NonBlank | None = None
    email: str | None = None
    phone: str | None = None
    enquirer_type: EnquirerType | None = None
    student_name: NonBlank | None = None
    student_age: Annotated[int | None, Field(gt=0, strict=True)] = None
    student_gender: StudentGender | None = None
    course_interest: NonBlank | None = None
    education_level: NonBlank | None = None
    preferred_mode: PreferredMode | None = None
    joining_timeline: JoiningTimeline | None = None
    joining_preference_details: NonBlank | None = None
    consent_to_contact: Annotated[
        bool | None,
        Field(strict=True, description="Unknown, granted, or refused/revoked"),
    ] = None

    @field_validator("email", "phone", mode="before")
    @classmethod
    def empty_contact_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.fullmatch(value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"[\s\-().]", "", value)
        if not _PHONE_RE.fullmatch(normalized):
            raise ValueError(
                "Phone must contain 10–15 digits with an optional leading +"
            )
        return normalized


class SourceKind(StrEnum):
    VISITOR_MESSAGE = "visitor_message"
    FORM_SUBMISSION = "form_submission"
    CONSENT_EVENT = "consent_event"
    COUNSELLOR_EVENT = "counsellor_event"


class Attribution(StrEnum):
    ENQUIRER_SELF = "enquirer_self"
    STUDENT_VIA_ENQUIRER = "student_via_enquirer"
    OTHER = "other"
    UNCLEAR = "unclear"


class FieldEvidence(ContractModel):
    source_id: NonBlank
    source_kind: SourceKind
    observed_at: AwareDatetime
    attribution: Attribution
    uncertain: Annotated[bool, Field(strict=True)]


class LeadPatch(ContractModel):
    set_values: LeadProfile = Field(default_factory=LeadProfile)
    clear_fields: list[LeadField] = Field(default_factory=list)
    corrected_fields: list[LeadField] = Field(default_factory=list)
    evidence: dict[LeadField, list[FieldEvidence]] = Field(default_factory=dict)

    @property
    def changed_fields(self) -> set[LeadField]:
        """Derived locally; never supplied by the extractor or serialized as input."""
        return {LeadField(name) for name in self.set_values.model_fields_set} | set(
            self.clear_fields
        )

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        set_fields = {LeadField(name) for name in self.set_values.model_fields_set}
        clear_fields = set(self.clear_fields)
        corrected_fields = set(self.corrected_fields)

        if len(clear_fields) != len(self.clear_fields):
            raise ValueError("clear_fields must not contain duplicates")
        if len(corrected_fields) != len(self.corrected_fields):
            raise ValueError("corrected_fields must not contain duplicates")
        if overlap := set_fields & clear_fields:
            raise ValueError(f"Cannot set and clear: {sorted(overlap)}")
        if unchanged := corrected_fields - self.changed_fields:
            raise ValueError(f"Corrections must be set or cleared: {sorted(unchanged)}")
        null_fields = {
            name for name in set_fields if getattr(self.set_values, name) is None
        }
        if null_fields:
            raise ValueError(f"Use clear_fields instead of None: {sorted(null_fields)}")
        missing = {name for name in self.changed_fields if not self.evidence.get(name)}
        if missing:
            raise ValueError(f"Missing evidence for: {sorted(missing)}")
        if extra := set(self.evidence) - self.changed_fields:
            raise ValueError(f"Evidence must refer to changed fields: {sorted(extra)}")
        return self


class Intent(StrEnum):
    UNKNOWN = "unknown"
    COURSE_INFO = "course_info"
    COURSE_RECOMMENDATION = "course_recommendation"
    ELIGIBILITY = "eligibility"
    FEES = "fees"
    BATCH_OR_SCHEDULE = "batch_or_schedule"
    APPLICATION = "application"
    COUNSELLING = "counselling"
    DEMO = "demo"
    CAMPUS_VISIT = "campus_visit"
    GENERAL_ENQUIRY = "general_enquiry"
    OUT_OF_SCOPE = "out_of_scope"
    OTHER = "other"


class QuestionResolution(StrEnum):
    ANSWERED = "answered"
    DECLINED = "declined"
    UNCLEAR = "unclear"
    NOT_ADDRESSED = "not_addressed"


class QuestionRecord(ContractModel):
    text: NonBlank
    target_fields: list[LeadField] = Field(default_factory=list)
    message_id: UUID
    asked_at: AwareDatetime


class CourseScopeRecord(ContractModel):
    """Discussion scope, not a declaration of the student's course selection."""

    course_names: list[NonBlank] = Field(default_factory=list)
    ambiguous: Annotated[bool, Field(strict=True)] = False
    evidence: list[FieldEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if (self.course_names or self.ambiguous) and not self.evidence:
            raise ValueError("Course scope needs evidence")
        return self


class ConflictRecord(ContractModel):
    field: LeadField
    accepted_value: FactValue
    proposed_value: FactValue
    accepted_evidence: list[FieldEvidence] = Field(default_factory=list)
    proposed_evidence: list[FieldEvidence] = Field(min_length=1)
    reason: NonBlank


class CurrentRequest(ContractModel):
    intent: Intent
    description: NonBlank | None = None
    evidence: list[FieldEvidence] = Field(min_length=1)


class Action(StrEnum):
    SCHEDULE_CALLBACK = "schedule_callback"
    SCHEDULE_DEMO = "schedule_demo"
    SCHEDULE_CAMPUS_VISIT = "schedule_campus_visit"
    APPLICATION_FOLLOW_UP = "application_follow_up"
    SEND_COURSE_DETAILS = "send_course_details"
    REVIEW_ELIGIBILITY = "review_eligibility"
    COUNSELLOR_HANDOVER = "counsellor_handover"
    OTHER = "other"


class ActionSignalType(StrEnum):
    REQUESTED = "requested"
    CANCELLED = "cancelled"
    MODIFIED = "modified"


class ActionSignal(ContractModel):
    """AI-proposed signal; does not establish a saved or confirmed action."""

    action_type: Action
    signal_type: ActionSignalType
    request_id: UUID | None = None  # Existing request only; application checks it.
    requested_time_text: NonBlank | None = None
    details: NonBlank | None = None
    evidence: list[FieldEvidence] = Field(min_length=1)


class ActionStatus(StrEnum):
    AWAITING_DETAILS = "awaiting_details"
    REQUEST_SAVED = "request_saved"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PendingAction(ContractModel):
    """Application-managed validated request, not an extractor output."""

    request_id: UUID  # Generated by application, retained across retries.
    action_type: Action
    status: ActionStatus = ActionStatus.AWAITING_DETAILS
    requested_time_text: NonBlank | None = None
    scheduled_for: AwareDatetime | None = None
    timezone: NonBlank | None = None  # IANA name checked by the scheduling service.
    details: NonBlank | None = None
    evidence: list[FieldEvidence] = Field(min_length=1)
    follow_up_id: UUID | None = None
    event_id: NonBlank | None = None

    @model_validator(mode="after")
    def require_operation_evidence(self) -> Self:
        if self.status != ActionStatus.AWAITING_DETAILS:
            if self.follow_up_id is None and self.event_id is None:
                raise ValueError(
                    "Action status needs a persisted follow-up or event ID"
                )
        return self


class ActionOutcome(ContractModel):
    """Reconcile against persisted operations; model output alone proves nothing."""

    request_id: UUID
    action_type: Action
    status: ActionStatus
    event_id: NonBlank | None = None
    follow_up_id: UUID | None = None
    details: NonBlank | None = None

    @model_validator(mode="after")
    def require_operation_evidence(self) -> Self:
        if self.status != ActionStatus.AWAITING_DETAILS:
            if self.follow_up_id is None and self.event_id is None:
                raise ValueError(
                    "Action outcome needs a persisted follow-up or event ID"
                )
        return self


class LeadTurnUpdate(ContractModel):
    patches: list[LeadPatch] = Field(default_factory=list)
    observed_intent: Intent | None = None
    intent_evidence: list[FieldEvidence] = Field(default_factory=list)
    declined_fields: dict[LeadField, list[FieldEvidence]] = Field(default_factory=dict)
    course_scope_update: CourseScopeRecord | None = None
    question_resolution: QuestionResolution | None = None
    question_resolution_evidence: list[FieldEvidence] = Field(default_factory=list)
    action_signals: list[ActionSignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_turn(self) -> Self:
        changed: set[LeadField] = set()
        for patch in self.patches:
            if overlap := changed & patch.changed_fields:
                raise ValueError(
                    f"Multiple patches change the same field: {sorted(overlap)}"
                )
            changed |= patch.changed_fields
        if self.observed_intent is not None and not self.intent_evidence:
            raise ValueError("Observed intent needs evidence")
        if self.observed_intent is None and self.intent_evidence:
            raise ValueError("Intent evidence needs an observed intent")
        if any(not evidence for evidence in self.declined_fields.values()):
            raise ValueError("Every declined field needs evidence")
        supplied = {
            LeadField(name)
            for patch in self.patches
            for name in patch.set_values.model_fields_set
        }
        if overlap := supplied & set(self.declined_fields):
            raise ValueError(
                f"Cannot supply and decline the same field: {sorted(overlap)}"
            )
        # Clearing saved data and declining future collection can occur together.
        resolved = self.question_resolution in {
            QuestionResolution.ANSWERED,
            QuestionResolution.DECLINED,
            QuestionResolution.UNCLEAR,
        }
        if resolved and not self.question_resolution_evidence:
            raise ValueError("Question resolution needs evidence")
        if self.question_resolution is None and self.question_resolution_evidence:
            raise ValueError("Question evidence needs a resolution")
        return self


class LeadCaptureState(ContractModel):
    """Application-managed conversation state, restored before extraction."""

    conversation_id: UUID
    lead_id: UUID | None = None
    revision: Annotated[int, Field(ge=0, strict=True)] = 0
    profile: LeadProfile = Field(default_factory=LeadProfile)
    field_evidence: dict[LeadField, list[FieldEvidence]] = Field(default_factory=dict)
    asked_fields: dict[LeadField, list[QuestionRecord]] = Field(default_factory=dict)
    declined_fields: dict[LeadField, list[FieldEvidence]] = Field(default_factory=dict)
    pending_question: QuestionRecord | None = None
    unresolved_conflicts: list[ConflictRecord] = Field(default_factory=list)
    active_course_scope: CourseScopeRecord = Field(default_factory=CourseScopeRecord)
    current_request: CurrentRequest | None = None
    # MVP: one active action; sequential requests must not silently replace it.
    pending_action: PendingAction | None = None

    @model_validator(mode="after")
    def validate_state_evidence(self) -> Self:
        for field, evidence in self.field_evidence.items():
            if not evidence:
                raise ValueError(f"Empty field evidence for {field}")
        for field, evidence in self.declined_fields.items():
            if not evidence:
                raise ValueError(f"Empty decline evidence for {field}")
        # Existing database facts may be loaded without historic provenance.
        # Do not invent evidence to satisfy a state-construction requirement.
        return self


class ConversationLeadSummary(ContractModel):
    conversation_id: UUID
    state_revision: Annotated[int, Field(ge=0, strict=True)]
    through_message_id: UUID | None = None
    generated_at: AwareDatetime
    fact_patches: list[LeadPatch] = Field(default_factory=list)
    primary_intent: Intent | None = None
    summary: NonBlank
    unresolved_questions: list[NonBlank] = Field(default_factory=list)
    unresolved_conflicts: list[ConflictRecord] = Field(default_factory=list)
    action_outcomes: list[ActionOutcome] = Field(default_factory=list)
    recommended_next_action: Action | None = None
    recommendation_reason: NonBlank | None = None

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        changed: set[LeadField] = set()
        for patch in self.fact_patches:
            if overlap := changed & patch.changed_fields:
                raise ValueError(f"Summary patches overlap: {sorted(overlap)}")
            changed |= patch.changed_fields
        has_action = self.recommended_next_action is not None
        has_reason = self.recommendation_reason is not None
        if has_action != has_reason:
            raise ValueError("Recommendation and reason must be supplied together")
        return self
