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
    """Base Pydantic model: rejects unknown fields (strict extractor contracts)."""

    model_config = ConfigDict(extra="forbid")


class EnquirerType(StrEnum):
    """Who is speaking in the conversation (student, parent, etc.)."""

    STUDENT = "student"
    PARENT = "parent"
    GUARDIAN = "guardian"
    OTHER = "other"


class StudentGender(StrEnum):
    """Student gender as reported by the enquirer."""

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class PreferredMode(StrEnum):
    """Preferred study delivery mode."""

    ONLINE = "online"
    OFFLINE = "offline"
    HYBRID = "hybrid"


class JoiningTimeline(StrEnum):
    """When the enquirer wants to start; map visitor phrasing to one value."""

    AS_SOON_AS_POSSIBLE = "as_soon_as_possible"
    NEXT_AVAILABLE_INTAKE = "next_available_intake"
    NEXT_ACADEMIC_YEAR = "next_academic_year"
    SPECIFIC_PERIOD = "specific_period"
    EXPLORING = "exploring"


class LeadField(StrEnum):
    """Keys for profile fields, patches, evidence, and merge decisions."""

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
    """Captured facts about the enquirer and student (all fields optional until set)."""

    name: NonBlank | None = Field(default=None, description="Enquirer's name.")
    email: str | None = Field(
        default=None, description="Contact email (syntax check only, not verified)."
    )
    phone: str | None = Field(
        default=None, description="Contact phone (10–15 digits, optional leading +)."
    )
    enquirer_type: EnquirerType | None = Field(
        default=None, description="Relationship of the speaker to the student."
    )
    student_name: NonBlank | None = Field(
        default=None, description="Student's name when enquirer is not the student."
    )
    student_age: Annotated[
        int | None, Field(gt=0, strict=True, description="Student age in years.")
    ] = None
    student_gender: StudentGender | None = Field(
        default=None, description="Student gender as reported."
    )
    course_interest: NonBlank | None = Field(
        default=None, description="Course or program the family is interested in."
    )
    education_level: NonBlank | None = Field(
        default=None, description="Current or target education level."
    )
    preferred_mode: PreferredMode | None = Field(
        default=None, description="Preferred online, offline, or hybrid study mode."
    )
    joining_timeline: JoiningTimeline | None = Field(
        default=None, description="When they want to start (ASAP, next intake, etc.)."
    )
    joining_preference_details: NonBlank | None = Field(
        default=None,
        description="Free-text detail for ``specific_period`` or similar timelines.",
    )
    consent_to_contact: Annotated[
        bool | None,
        Field(
            strict=True,
            description="Unknown, granted, or refused/revoked contact consent.",
        ),
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
    """Type of persisted event that supports a field claim."""

    VISITOR_MESSAGE = "visitor_message"
    FORM_SUBMISSION = "form_submission"
    CONSENT_EVENT = "consent_event"
    COUNSELLOR_EVENT = "counsellor_event"


class Attribution(StrEnum):
    """Who the extracted fact is attributed to."""

    ENQUIRER_SELF = "enquirer_self"
    STUDENT_VIA_ENQUIRER = "student_via_enquirer"
    OTHER = "other"
    UNCLEAR = "unclear"


class FieldEvidence(ContractModel):
    """Provenance for one extracted fact, tied to a trusted source event."""

    source_id: NonBlank = Field(
        description="ID of the trusted source record (message, form, etc.)."
    )
    source_kind: SourceKind = Field(description="Kind of source event.")
    observed_at: AwareDatetime = Field(
        description="When the fact was observed (timezone-aware)."
    )
    attribution: Attribution = Field(description="Who the fact is attributed to.")
    uncertain: Annotated[
        bool, Field(strict=True, description="True if extraction confidence is low.")
    ]


class LeadPatch(ContractModel):
    """One batch of profile set/clear operations with evidence."""

    set_values: LeadProfile = Field(
        default_factory=LeadProfile,
        description="Profile fields to set (only provided keys are changed).",
    )
    clear_fields: list[LeadField] = Field(
        default_factory=list,
        description="Profile fields to clear explicitly.",
    )
    corrected_fields: list[LeadField] = Field(
        default_factory=list,
        description="Fields replaced despite an existing different value.",
    )
    evidence: dict[LeadField, list[FieldEvidence]] = Field(
        default_factory=dict,
        description="Supporting evidence per changed field.",
    )

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
    """Classified goal of the visitor's current request."""

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
    """How a pending bot question was addressed this turn."""

    ANSWERED = "answered"
    DECLINED = "declined"
    UNCLEAR = "unclear"
    NOT_ADDRESSED = "not_addressed"


class QuestionRecord(ContractModel):
    """Assistant question asked to collect specific profile fields."""

    text: NonBlank = Field(description="Question text shown to the visitor.")
    target_fields: list[LeadField] = Field(
        default_factory=list,
        description="Profile fields this question is trying to collect.",
    )
    message_id: UUID = Field(description="Persisted assistant message ID.")
    asked_at: AwareDatetime = Field(
        description="When the question was asked (timezone-aware)."
    )


class CourseScopeRecord(ContractModel):
    """Discussion scope for courses mentioned; not the student's committed choice."""

    course_names: list[NonBlank] = Field(
        default_factory=list,
        description="Course names under discussion this turn.",
    )
    ambiguous: Annotated[
        bool,
        Field(strict=True, description="True if scope is unclear or multi-course."),
    ] = False
    evidence: list[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence supporting the scope update.",
    )

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if (self.course_names or self.ambiguous) and not self.evidence:
            raise ValueError("Course scope needs evidence")
        return self


class ConflictRecord(ContractModel):
    """Unresolved disagreement between accepted and proposed profile values."""

    field: LeadField = Field(description="Profile field in conflict.")
    accepted_value: FactValue = Field(description="Currently accepted value.")
    proposed_value: FactValue = Field(
        description="New value without explicit correction."
    )
    accepted_evidence: list[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence for the accepted value.",
    )
    proposed_evidence: list[FieldEvidence] = Field(
        min_length=1,
        description="Evidence for the proposed value.",
    )
    reason: NonBlank = Field(description="Why the conflict was recorded.")


class CurrentRequest(ContractModel):
    """Visitor's active conversational goal for this thread."""

    intent: Intent = Field(description="Classified intent for the current request.")
    description: NonBlank | None = Field(
        default=None, description="Optional human-readable summary of the request."
    )
    evidence: list[FieldEvidence] = Field(
        min_length=1, description="Evidence supporting the intent classification."
    )


class Action(StrEnum):
    """Follow-up operations the visitor may request (callback, demo, etc.)."""

    SCHEDULE_CALLBACK = "schedule_callback"
    SCHEDULE_DEMO = "schedule_demo"
    SCHEDULE_CAMPUS_VISIT = "schedule_campus_visit"
    APPLICATION_FOLLOW_UP = "application_follow_up"
    SEND_COURSE_DETAILS = "send_course_details"
    REVIEW_ELIGIBILITY = "review_eligibility"
    COUNSELLOR_HANDOVER = "counsellor_handover"
    OTHER = "other"


class ActionSignalType(StrEnum):
    """Whether an action was requested, modified, or cancelled."""

    REQUESTED = "requested"
    CANCELLED = "cancelled"
    MODIFIED = "modified"


class ActionSignal(ContractModel):
    """AI-proposed action signal; does not persist until the handler accepts it."""

    action_type: Action = Field(description="Kind of follow-up action.")
    signal_type: ActionSignalType = Field(
        description="Requested, modified, or cancelled."
    )
    request_id: UUID | None = Field(
        default=None,
        description="Existing pending request ID (required for modify/cancel).",
    )
    requested_time_text: NonBlank | None = Field(
        default=None, description="Visitor's natural-language time preference."
    )
    details: NonBlank | None = Field(
        default=None, description="Extra details for the action."
    )
    evidence: list[FieldEvidence] = Field(
        min_length=1, description="Evidence supporting this signal."
    )


class ActionStatus(StrEnum):
    """Lifecycle state of a pending or completed action."""

    AWAITING_DETAILS = "awaiting_details"
    REQUEST_SAVED = "request_saved"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PendingAction(ContractModel):
    """Application-managed validated request, not an extractor output."""

    request_id: UUID = Field(
        description="Application-generated ID retained across retries."
    )
    action_type: Action = Field(description="Kind of follow-up action.")
    status: ActionStatus = Field(
        default=ActionStatus.AWAITING_DETAILS,
        description="Current lifecycle state of the action.",
    )
    requested_time_text: NonBlank | None = Field(
        default=None, description="Visitor's natural-language time preference."
    )
    scheduled_for: AwareDatetime | None = Field(
        default=None, description="Resolved schedule time when known."
    )
    timezone: NonBlank | None = Field(
        default=None, description="IANA timezone name for scheduling."
    )
    details: NonBlank | None = Field(
        default=None, description="Extra details for the action."
    )
    evidence: list[FieldEvidence] = Field(
        min_length=1, description="Evidence from when the action was requested."
    )
    follow_up_id: UUID | None = Field(
        default=None, description="Persisted follow-up record ID when saved."
    )
    event_id: NonBlank | None = Field(
        default=None, description="Calendar or external event ID when scheduled."
    )

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

    request_id: UUID = Field(description="Action request this outcome refers to.")
    action_type: Action = Field(description="Kind of follow-up action.")
    status: ActionStatus = Field(description="Final or intermediate action status.")
    event_id: NonBlank | None = Field(
        default=None, description="Calendar or external event ID when scheduled."
    )
    follow_up_id: UUID | None = Field(
        default=None, description="Persisted follow-up record ID."
    )
    details: NonBlank | None = Field(
        default=None, description="Outcome details from the handler."
    )

    @model_validator(mode="after")
    def require_operation_evidence(self) -> Self:
        if self.status != ActionStatus.AWAITING_DETAILS:
            if self.follow_up_id is None and self.event_id is None:
                raise ValueError(
                    "Action outcome needs a persisted follow-up or event ID"
                )
        return self


class LeadTurnUpdate(ContractModel):
    """Extractor output for one conversation turn (validated before merge)."""

    patches: list[LeadPatch] = Field(
        default_factory=list,
        description="Profile field updates proposed this turn.",
    )
    observed_intent: Intent | None = Field(
        default=None, description="Classified intent for this turn, if any."
    )
    intent_evidence: list[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence for ``observed_intent``.",
    )
    declined_fields: dict[LeadField, list[FieldEvidence]] = Field(
        default_factory=dict,
        description="Fields the visitor refused to provide, with evidence.",
    )
    course_scope_update: CourseScopeRecord | None = Field(
        default=None, description="Updated course discussion scope, if any."
    )
    question_resolution: QuestionResolution | None = Field(
        default=None, description="How the pending question was addressed."
    )
    question_resolution_evidence: list[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence for question resolution.",
    )
    action_signals: list[ActionSignal] = Field(
        default_factory=list,
        description="Action request/modify/cancel signals this turn.",
    )

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

    conversation_id: UUID = Field(
        description="Conversation this lead state belongs to."
    )
    lead_id: UUID | None = Field(
        default=None, description="Dashboard lead row ID when linked."
    )
    revision: Annotated[
        int,
        Field(
            ge=0, strict=True, description="Optimistic-lock revision; bump on change."
        ),
    ] = 0
    profile: LeadProfile = Field(
        default_factory=LeadProfile, description="Accepted profile field values."
    )
    field_evidence: dict[LeadField, list[FieldEvidence]] = Field(
        default_factory=dict,
        description="Provenance per accepted profile field.",
    )
    asked_fields: dict[LeadField, list[QuestionRecord]] = Field(
        default_factory=dict,
        description="History of questions asked per field.",
    )
    declined_fields: dict[LeadField, list[FieldEvidence]] = Field(
        default_factory=dict,
        description="Refusals to answer, with evidence.",
    )
    pending_question: QuestionRecord | None = Field(
        default=None, description="Open bot question awaiting an answer."
    )
    unresolved_conflicts: list[ConflictRecord] = Field(
        default_factory=list,
        description="Profile value conflicts not yet resolved.",
    )
    active_course_scope: CourseScopeRecord = Field(
        default_factory=CourseScopeRecord,
        description="Courses currently under discussion.",
    )
    current_request: CurrentRequest | None = Field(
        default=None, description="Visitor's active classified request."
    )
    pending_action: PendingAction | None = Field(
        default=None,
        description="Single in-flight action (MVP: one active action per conversation).",
    )

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
    """Counsellor-facing rollup generated from lead capture state."""

    conversation_id: UUID = Field(description="Conversation summarized.")
    state_revision: Annotated[
        int,
        Field(
            ge=0, strict=True, description="Lead state revision this summary reflects."
        ),
    ]
    through_message_id: UUID | None = Field(
        default=None, description="Last message included in the summary, if bounded."
    )
    generated_at: AwareDatetime = Field(
        description="When the summary was generated (timezone-aware)."
    )
    fact_patches: list[LeadPatch] = Field(
        default_factory=list,
        description="Fact changes included in this summary window.",
    )
    primary_intent: Intent | None = Field(
        default=None, description="Dominant visitor intent in the window."
    )
    summary: NonBlank = Field(description="Narrative summary for counsellors.")
    unresolved_questions: list[NonBlank] = Field(
        default_factory=list,
        description="Questions still open after the window.",
    )
    unresolved_conflicts: list[ConflictRecord] = Field(
        default_factory=list,
        description="Conflicts still open after the window.",
    )
    action_outcomes: list[ActionOutcome] = Field(
        default_factory=list,
        description="Action handler outcomes in the window.",
    )
    recommended_next_action: Action | None = Field(
        default=None, description="Suggested follow-up action for staff."
    )
    recommendation_reason: NonBlank | None = Field(
        default=None, description="Why the next action was recommended."
    )

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
