from __future__ import annotations

import datetime
import uuid
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for Dashboard DB ORM models."""

    pass


class OtpType(StrEnum):
    """Allowed delivery channels for one-time passwords."""

    EMAIL = "EMAIL"
    MOBILE = "MOBILE"


class OtpPurpose(StrEnum):
    """Reason a one-time password was issued."""

    LOGIN = "LOGIN"
    VERIFY_EMAIL = "VERIFY_EMAIL"
    VERIFY_PHONE = "VERIFY_PHONE"
    RESET_PASSWORD = "RESET_PASSWORD"


class Organizations(Base):
    """Organization and tenant record.

    Attributes:
        id: Organization identifier.
        created_at: Creation timestamp.
        is_email_verified: Whether the organization email is verified.
        name: Display name.
        logo_url: Organization logo URL.
        address: Structured address data.
        email: Organization email.
        phone: Organization phone number.
        email_token: Email-verification token.
        bots: Organization bots.
        organization_members: Organization membership records.
        invites: Pending organization invitations.
        magic_links: Organization-scoped magic links.
        api_keys: Organization API keys.
        files: Organization files.
        training_sources: Organization training sources.
        conversations_meta: Organization conversations.
        leads: Organization leads.
    """

    __tablename__ = "organizations"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "public"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    name: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    address: Mapped[dict | None] = mapped_column(JSONB)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email_token: Mapped[str | None] = mapped_column(Text)

    bots: Mapped[list[Bots]] = relationship("Bots", back_populates="organization")
    organization_members: Mapped[list[OrganizationMembers]] = relationship(
        "OrganizationMembers", back_populates="organization"
    )
    magic_links: Mapped[list[MagicLinks]] = relationship(
        "MagicLinks", back_populates="organization"
    )
    invites: Mapped[list[OrganizationInvites]] = relationship(
        "OrganizationInvites", back_populates="organization"
    )
    api_keys: Mapped[list[ApiKeys]] = relationship(
        "ApiKeys", back_populates="organization"
    )
    files: Mapped[list[Files]] = relationship("Files", back_populates="organization")
    training_sources: Mapped[list[TrainingSources]] = relationship(
        "TrainingSources", back_populates="organization"
    )
    conversations_meta: Mapped[list[ConversationsMeta]] = relationship(
        "ConversationsMeta", back_populates="organization"
    )
    leads: Mapped[list[Leads]] = relationship("Leads", back_populates="organization")


class Bots(Base):
    """Bot configuration and organization-facing profile.

    Attributes:
        id: Bot identifier.
        created_at: Creation timestamp.
        name: Bot display name.
        institute_name: Institution name shown by the bot.
        capture_leads: Whether lead capture is enabled.
        updated_at: Last update timestamp.
        organization_id: Owning organization identifier.
        tone: Response tone.
        role: Bot role.
        business_description: Institution description.
        first_message: Initial greeting.
        confirmation_message: Lead confirmation message.
        lead_capture_message: Lead-capture prompt.
        lead_capture_timing: When lead capture occurs.
        capture_name: Whether to capture names.
        capture_email: Whether to capture email addresses.
        capture_phone: Whether to capture phone numbers.
        deleted_at: Soft-deletion timestamp.
        deleted_by: Identifier of the deleter or system marker.
        organization: Owning organization.
        api_keys: Bot API keys.
        files: Bot files.
        training_sources: Bot training sources.
        conversations_meta: Bot conversations.
        leads: Bot leads.
    """

    __tablename__ = "bots"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    institute_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_leads: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=text("now()"),
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE"),
    )
    tone: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    business_description: Mapped[str | None] = mapped_column(Text)
    first_message: Mapped[str | None] = mapped_column(Text)
    confirmation_message: Mapped[str | None] = mapped_column(Text)
    lead_capture_message: Mapped[str | None] = mapped_column(Text)
    lead_capture_timing: Mapped[str | None] = mapped_column(Text)
    capture_name: Mapped[bool | None] = mapped_column(
        Boolean, server_default=text("false")
    )
    capture_email: Mapped[bool | None] = mapped_column(
        Boolean, server_default=text("false")
    )
    capture_phone: Mapped[bool | None] = mapped_column(
        Boolean, server_default=text("false")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="bots"
    )
    api_keys: Mapped[list[ApiKeys]] = relationship("ApiKeys", back_populates="bot")
    files: Mapped[list[Files]] = relationship("Files", back_populates="bot")
    training_sources: Mapped[list[TrainingSources]] = relationship(
        "TrainingSources", back_populates="bot"
    )
    conversations_meta: Mapped[list[ConversationsMeta]] = relationship(
        "ConversationsMeta", back_populates="bot"
    )
    leads: Mapped[list[Leads]] = relationship("Leads", back_populates="bot")


class Users(Base):
    """Dashboard user and authentication profile.

    Attributes:
        id: User identifier.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        email: Primary email address.
        email_verified: Whether the email is verified.
        email_verified_at: Email verification timestamp.
        password_hash: Hashed password.
        phone: Phone number.
        phone_verified: Whether the phone is verified.
        phone_verified_at: Phone verification timestamp.
        full_name: User's full name.
        avatar_url: Avatar URL.
        google_id: Google account identifier.
        github_id: GitHub account identifier.
        microsoft_id: Microsoft account identifier.
        google_email: Google account email.
        github_email: GitHub account email.
        microsoft_email: Microsoft account email.
        last_logged_in: Last login timestamp.
        last_logged_in_ip: Last login IP address.
        is_active: Whether the account is active.
        is_banned: Whether the account is banned.
        banned_until: Ban expiration timestamp.
        onboarding_completed: Whether onboarding is complete.
        organization_members: Organization memberships.
        otps: One-time passwords issued to the user.
        magic_links: Magic links issued to the user.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("users_email_idx", "email"),
        Index("users_google_id_idx", "google_id"),
        Index("users_github_id_idx", "github_id"),
        Index("users_microsoft_id_idx", "microsoft_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=text("now()"),
    )

    email: Mapped[str | None] = mapped_column(Text, unique=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    email_verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    password_hash: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    phone_verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    full_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)

    google_id: Mapped[str | None] = mapped_column(Text, unique=True)
    github_id: Mapped[str | None] = mapped_column(Text, unique=True)
    microsoft_id: Mapped[str | None] = mapped_column(Text, unique=True)

    google_email: Mapped[str | None] = mapped_column(Text)
    github_email: Mapped[str | None] = mapped_column(Text)
    microsoft_email: Mapped[str | None] = mapped_column(Text)

    last_logged_in: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    last_logged_in_ip: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    is_banned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    banned_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    organization_members: Mapped[list[OrganizationMembers]] = relationship(
        "OrganizationMembers", back_populates="user"
    )
    otps: Mapped[list[Otps]] = relationship("Otps", back_populates="user")
    magic_links: Mapped[list[MagicLinks]] = relationship(
        "MagicLinks", back_populates="user"
    )


class MagicLinks(Base):
    """Single-use authentication link.

    Attributes:
        id: Magic-link identifier.
        created_at: Creation timestamp.
        user_id: Target user identifier.
        organization_id: Optional organization scope.
        token_hash: Hashed link token.
        expires_at: Expiration timestamp.
        used_at: Consumption timestamp.
        user: Target user.
        organization: Optional organization scope.
    """

    __tablename__ = "magic_links"
    __table_args__ = (
        Index("magic_links_user_id_idx", "user_id"),
        Index("magic_links_organization_id_idx", "organization_id"),
        Index("magic_links_expires_at_idx", "expires_at"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False
    )
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    user: Mapped[Users] = relationship("Users", back_populates="magic_links")
    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="magic_links"
    )


class OrganizationMembers(Base):
    """Membership connecting a user to an organization.

    Attributes:
        id: Membership identifier.
        created_at: Creation timestamp.
        organization_id: Organization identifier.
        role: Organization role.
        user_id: User identifier.
        organization: Related organization.
        user: Related user.
    """

    __tablename__ = "organization_members"
    __table_args__ = (
        Index("organization_members_user_id_idx", "user_id"),
        Index("organization_members_organization_id_idx", "organization_id"),
        UniqueConstraint("organization_id", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE"),
        server_default=text("''"),
    )
    role: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.users.id", ondelete="CASCADE")
    )

    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="organization_members"
    )
    user: Mapped[Users | None] = relationship(
        "Users", back_populates="organization_members"
    )


class OrganizationInvites(Base):
    """Invitation for a user to join an organization.

    Attributes:
        id: Invitation identifier.
        organization_id: Organization identifier.
        email: Invited email address.
        role: Role granted when accepted.
        created_at: Creation timestamp.
        accepted_at: Acceptance timestamp.
        organization: Related organization.
    """

    __tablename__ = "organization_invites"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'editor'")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    accepted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    organization: Mapped[Organizations] = relationship(
        "Organizations", back_populates="invites"
    )


class Otps(Base):
    """One-time password record for authentication or verification.

    Attributes:
        id: OTP identifier.
        created_at: Creation timestamp.
        user_id: Optional target user identifier.
        code: Hashed OTP code.
        type: Email or mobile delivery type.
        purpose: OTP purpose.
        email: Target email address.
        phone: Target phone number.
        expires_at: Expiration timestamp.
        used_at: Consumption timestamp.
        is_used: Whether the OTP was consumed.
        attempts: Number of verification attempts.
        max_attempts: Maximum allowed attempts.
        ip_address: Request IP address.
        user_agent: Request user agent.
        user: Related user.
    """

    __tablename__ = "otps"
    __table_args__ = (
        Index("otps_user_id_idx", "user_id"),
        Index("otps_email_idx", "email"),
        Index("otps_phone_idx", "phone"),
        Index("otps_code_idx", "code"),
        Index("otps_expires_at_idx", "expires_at"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.users.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[OtpType] = mapped_column(
        SqlEnum(OtpType, name="OtpType", schema="public"),
        nullable=False,
    )
    purpose: Mapped[OtpPurpose] = mapped_column(
        SqlEnum(OtpPurpose, name="OtpPurpose", schema="public"),
        nullable=False,
    )

    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False
    )
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    is_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("5"))
    ip_address: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped[Users | None] = relationship("Users", back_populates="otps")


class Notifications(Base):
    """Dashboard notification delivered to a user.

    Attributes:
        id: Notification identifier.
        organization_id: Organization identifier.
        user_id: Target user identifier.
        title: Notification title.
        body: Notification body.
        type: Notification type.
        read_at: Read timestamp.
        created_at: Creation timestamp.
        metadata_json: Additional notification data.
        channels: Delivery channels.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "notifications_user_id_read_at_created_at_idx",
            "user_id",
            "read_at",
            text("created_at DESC"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    # "metadata" is reserved by SQLAlchemy's declarative base.
    metadata_json: Mapped[Any | None] = mapped_column("metadata", JSONB)
    channels: Mapped[Any | None] = mapped_column(JSONB)
    # ! email | sms | webhook | dashboard


class ApiKeys(Base):
    """API key metadata for an organization or bot.

    Attributes:
        id: API-key identifier.
        name: Display name.
        key_hash: Hashed API key.
        created_at: Creation timestamp.
        organization_id: Optional organization identifier.
        bot_id: Optional bot identifier.
        is_active: Whether the key is active.
        last_used_at: Last usage timestamp.
        bot: Related bot.
        organization: Related organization.
        conversations_meta: Conversations using the key.
    """

    __tablename__ = "api_keys"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="CASCADE")
    )
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )

    bot: Mapped[Bots | None] = relationship("Bots", back_populates="api_keys")
    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="api_keys"
    )
    conversations_meta: Mapped[list[ConversationsMeta]] = relationship(
        "ConversationsMeta", back_populates="api_key"
    )


class Files(Base):
    """Uploaded file metadata stored in dashboard storage.

    Attributes:
        id: File identifier.
        created_at: Creation timestamp.
        organization_id: Organization identifier.
        bot_id: Bot identifier.
        provider: Storage provider.
        bucket: Storage bucket.
        path: Storage object path.
        original_filename: Original upload filename.
        mime_type: Uploaded MIME type.
        size_bytes: File size in bytes.
        purpose: File purpose.
        status: File processing status.
        deleted_at: Soft-deletion timestamp.
        deleted_by: Identifier of the deleter.
        bot: Related bot.
        organization: Related organization.
    """

    __tablename__ = "files"
    __table_args__ = (
        Index("files_org_bot_path_idx", "organization_id", "bot_id", "path"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="CASCADE")
    )
    provider: Mapped[str | None] = mapped_column(Text, server_default=text("'r2'"))
    bucket: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    purpose: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)

    bot: Mapped[Bots | None] = relationship("Bots", back_populates="files")
    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="files"
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_by: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrainingSources(Base):
    """Source selected for bot training.

    Attributes:
        id: Training-source identifier.
        created_at: Creation timestamp.
        organization_id: Organization identifier.
        bot_id: Bot identifier.
        type: Source type, such as URL or file.
        status: Training-source lifecycle status.
        error_message: Processing failure details.
        source_value: URL or source reference.
        content_hash: Content hash for deduplication.
        original_filename: Original file name.
        size_bytes: Source size in bytes.
        mime_type: Source MIME type.
        updated_at: Last update timestamp.
        last_trained_at: Last successful training timestamp.
        deleted_at: Soft-deletion timestamp.
        deleted_by: Identifier of the deleter.
        quality_status: Quality classification.
        quality_summary: Quality analysis details.
        bot: Related bot.
        organization: Related organization.
    """

    __tablename__ = "training_sources"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="CASCADE")
    )
    type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    source_value: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=text("now()"),
    )
    last_trained_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_status: Mapped[str | None] = mapped_column(Text)
    # ! good | warning | poor
    quality_summary: Mapped[Any | None] = mapped_column(JSONB)
    bot: Mapped[Bots | None] = relationship("Bots", back_populates="training_sources")
    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="training_sources"
    )


class ConversationsMeta(Base):
    """Conversation state and dashboard metadata.

    Attributes:
        id: Conversation identifier.
        created_at: Creation timestamp.
        mode: Conversation mode.
        organization_id: Organization identifier.
        bot_id: Bot identifier.
        lead_id: Associated lead identifier.
        api_key_id: API key identifier.
        user_name: Visitor name.
        user_email: Visitor email.
        status: Conversation status.
        is_archived: Whether the conversation is archived.
        handover_status: Support handover status.
        closed_by: Actor that closed the conversation.
        closed_at: Closure timestamp.
        last_message_snippet: Latest message preview.
        last_message_at: Latest message timestamp.
        api_key: Related API key.
        bot: Related bot.
        lead: Related lead.
        organization: Related organization.
    """

    __tablename__ = "conversations_meta"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'ai'")
    )  # ! ai | human
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="SET NULL")
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.leads.id", ondelete="SET NULL")
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.api_keys.id", ondelete="SET NULL")
    )
    user_name: Mapped[str | None] = mapped_column(Text)
    user_email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'open'")
    )
    # ! open | closed
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    handover_status: Mapped[str | None] = mapped_column(
        Text, server_default=text("'none'")
    )
    # ! none | requested | accepted | timed_out
    closed_by: Mapped[str | None] = mapped_column(Text)
    # ! visitor | support_agent | system | admin (admin -> when org disabled or bot deleted or other circumstances)
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    last_message_snippet: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    lead_capture_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    summary_status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    summary_through_message_id: Mapped[uuid.UUID | None] = mapped_column()
    summary_generated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(False)
    )

    api_key: Mapped[ApiKeys | None] = relationship(
        "ApiKeys", back_populates="conversations_meta"
    )
    bot: Mapped[Bots | None] = relationship("Bots", back_populates="conversations_meta")
    lead: Mapped[Leads | None] = relationship("Leads", back_populates="conversations")
    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="conversations_meta"
    )


class LeadFollowUps(Base):
    """Scheduled follow-up action for a lead.

    Attributes:
        id: Follow-up identifier.
        source_request_id: Idempotent user request identifier.
        counsellor_id: Assigned counsellor identifier.
        follow_up_type: Follow-up action type.
        status: Follow-up lifecycle status.
        scheduled_for: Scheduled execution timestamp.
        completed_at: Completion timestamp.
        outcome: Follow-up outcome.
        notes: Follow-up notes.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        lead_id: Associated lead identifier.
        deleted_at: Soft-deletion timestamp.
        deleted_by_id: Identifier of the deleter.
        lead: Related lead.
    """

    __tablename__ = "lead_follow_ups"
    __table_args__ = (
        Index("lead_follow_ups_lead_id_idx", "lead_id"),
        UniqueConstraint(
            "source_request_id", name="lead_follow_ups_source_request_key"
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_request_id: Mapped[uuid.UUID | None] = mapped_column()
    counsellor_id: Mapped[uuid.UUID | None] = mapped_column()
    follow_up_type: Mapped[str | None] = mapped_column(Text)
    # ! callback | demo | campus_visit | application_follow_up | send_course_details | review_elligibility | other
    status: Mapped[str | None] = mapped_column(Text)
    # ! pending | complete | cancelled
    scheduled_for: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    outcome: Mapped[str | None] = mapped_column(Text)
    # ! contacted | not_interested | interested | no_answer | follow_up_again | application_started | enrolled | lost
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=text("now()"),
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("public.leads.id", ondelete="CASCADE"), nullable=False
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column()

    lead: Mapped[Leads] = relationship("Leads", back_populates="lead_follow_ups")


class Leads(Base):
    """Captured lead and qualification information.

    Attributes:
        id: Lead identifier.
        name: Enquirer name.
        email: Enquirer email.
        phone: Enquirer phone.
        enquirer_type: Enquirer relationship or type.
        student_name: Student name.
        student_age: Student age.
        student_gender: Student gender.
        course_interest: Course of interest.
        education_level: Education level.
        preferred_mode: Preferred study mode.
        joining_timeline: Expected joining timeline.
        primary_intent: Primary enquiry intent.
        lead_priority: Lead priority.
        priority_reason: Reason for the priority.
        ai_summary: AI-generated lead summary.
        recommended_next_action: Recommended next action.
        pipeline_stage: Lead pipeline stage.
        consent_to_contact: Whether contact consent was given.
        updated_at: Last update timestamp.
        captured_at: Capture timestamp.
        deleted_at: Soft-deletion timestamp.
        deleted_by_id: Identifier of the deleter.
        visitor_id: Visitor identifier.
        organization_id: Organization identifier.
        bot_id: Bot identifier.
        bot: Related bot.
        conversations: Conversations associated with the lead.
        organization: Related organization.
        lead_follow_ups: Scheduled follow-ups.
    """

    __tablename__ = "leads"
    __table_args__ = (
        Index(
            "leads_active_visitor_key",
            "organization_id",
            "bot_id",
            "visitor_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str | None] = mapped_column(Text)  # ! enquirer name
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    enquirer_type: Mapped[str | None] = mapped_column(Text)
    # ! parent | guardian | student | friend | relative | other
    student_name: Mapped[str | None] = mapped_column(Text)
    student_age: Mapped[str | None] = mapped_column(Text)
    student_gender: Mapped[str | None] = mapped_column(Text)
    course_interest: Mapped[str | None] = mapped_column(Text)
    education_level: Mapped[str | None] = mapped_column(Text)
    preferred_mode: Mapped[str | None] = mapped_column(Text)
    # ! online | offline | both
    joining_timeline: Mapped[str | None] = mapped_column(Text)
    # ! immediate | within_1_week | within_1_month | within_3_months | beyond_3_months
    joining_preference_details: Mapped[str | None] = mapped_column(Text)
    profile_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    profile_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    primary_intent: Mapped[str | None] = mapped_column(Text)
    # ! course_info | course_recommendation | elligibility | fees | batch_or_schedule | application | counselling | demo | campus_visit | general_enquiry | other
    lead_priority: Mapped[str | None] = mapped_column(Text)
    # ! hot | warm | cold
    priority_reason: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    recommended_next_action: Mapped[str | None] = mapped_column(Text)
    # ! schedule_callback | schedule_demo | schedule_campus_visit | application_follow_up | send_course_details | review_elligibility | other
    pipeline_stage: Mapped[str | None] = mapped_column(Text)
    # ! new_enquiry | qualified | counselling_requested | contacted | application_started | enrolled | lost
    consent_to_contact: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=text("now()"),
    )
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    deleted_by_id: Mapped[uuid.UUID | None] = mapped_column()
    visitor_id: Mapped[uuid.UUID | None] = mapped_column()
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="SET NULL")
    )
    bot: Mapped[Bots | None] = relationship("Bots", back_populates="leads")
    conversations: Mapped[list[ConversationsMeta]] = relationship(
        "ConversationsMeta", back_populates="lead"
    )
    organization: Mapped[Organizations | None] = relationship(
        "Organizations", back_populates="leads"
    )
    lead_follow_ups: Mapped[list[LeadFollowUps]] = relationship(
        "LeadFollowUps", back_populates="lead"
    )


# Billing mirrors: dashboard Prisma migrations remain the schema authority.


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    __table_args__ = {"schema": "public"}
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, primary_key=True, nullable=False
    )
    plan_id: Mapped[str] = mapped_column("plan_id", Text, nullable=False)
    plan_version: Mapped[str] = mapped_column("plan_version", Text, nullable=False)
    limits: Mapped[dict] = mapped_column("limits", JSONB, nullable=False)
    status: Mapped[str] = mapped_column("status", Text, nullable=False)
    trial_ends_at: Mapped[datetime.datetime] = mapped_column(
        "trial_ends_at", DateTime(True), nullable=False
    )
    paid_through: Mapped[datetime.datetime | None] = mapped_column(
        "paid_through", DateTime(True), nullable=True
    )
    grace_until: Mapped[datetime.datetime | None] = mapped_column(
        "grace_until", DateTime(True), nullable=True
    )
    current_period_id: Mapped[uuid.UUID | None] = mapped_column(
        "current_period_id", UUID(as_uuid=True), nullable=True
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(
        "provider_subscription_id", Text, nullable=True
    )
    provider_status: Mapped[str | None] = mapped_column(
        "provider_status", Text, nullable=True
    )
    provider_updated_at: Mapped[datetime.datetime | None] = mapped_column(
        "provider_updated_at", DateTime(True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        "cancel_at_period_end", Boolean, nullable=False
    )
    pending_plan_id: Mapped[str | None] = mapped_column(
        "pending_plan_id", Text, nullable=True
    )
    billing_details: Mapped[dict] = mapped_column(
        "billing_details", JSONB, nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        "created_at", DateTime(True), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        "updated_at", DateTime(True), nullable=False
    )


class BillingTrialClaim(Base):
    __tablename__ = "billing_trial_claims"
    __table_args__ = {"schema": "public"}
    user_id: Mapped[uuid.UUID] = mapped_column(
        "user_id", UUID(as_uuid=True), primary_key=True, nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    claimed_at: Mapped[datetime.datetime] = mapped_column(
        "claimed_at", DateTime(True), nullable=False
    )


class BillingPeriod(Base):
    __tablename__ = "billing_periods"
    __table_args__ = {"schema": "public"}
    id: Mapped[uuid.UUID] = mapped_column(
        "id", UUID(as_uuid=True), primary_key=True, nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    plan_id: Mapped[str] = mapped_column("plan_id", Text, nullable=False)
    plan_version: Mapped[str] = mapped_column("plan_version", Text, nullable=False)
    starts_at: Mapped[datetime.datetime] = mapped_column(
        "starts_at", DateTime(True), nullable=False
    )
    ends_at: Mapped[datetime.datetime] = mapped_column(
        "ends_at", DateTime(True), nullable=False
    )
    reply_limit: Mapped[int] = mapped_column("reply_limit", Integer, nullable=False)
    replies_used: Mapped[int] = mapped_column("replies_used", Integer, nullable=False)
    replies_reserved: Mapped[int] = mapped_column(
        "replies_reserved", Integer, nullable=False
    )
    training_limit: Mapped[int] = mapped_column(
        "training_limit", BigInteger, nullable=False
    )
    training_used: Mapped[int] = mapped_column(
        "training_used", BigInteger, nullable=False
    )
    training_reserved: Mapped[int] = mapped_column(
        "training_reserved", BigInteger, nullable=False
    )
    payment_id: Mapped[str | None] = mapped_column("payment_id", Text, nullable=True)


class BillingCheckout(Base):
    __tablename__ = "billing_checkouts"
    __table_args__ = {"schema": "public"}
    id: Mapped[uuid.UUID] = mapped_column(
        "id", UUID(as_uuid=True), primary_key=True, nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    plan_id: Mapped[str] = mapped_column("plan_id", Text, nullable=False)
    kind: Mapped[str] = mapped_column("kind", Text, nullable=False)
    provider_id: Mapped[str] = mapped_column("provider_id", Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column("session_id", Text, nullable=True)
    billing_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column("status", Text, nullable=False)
    subtotal_paise: Mapped[int] = mapped_column(
        "subtotal_paise", Integer, nullable=False
    )
    tax_paise: Mapped[int] = mapped_column("tax_paise", Integer, nullable=False)
    total_paise: Mapped[int] = mapped_column("total_paise", Integer, nullable=False)
    tax_bps: Mapped[int] = mapped_column("tax_bps", Integer, nullable=False)
    first_charge_at: Mapped[datetime.datetime] = mapped_column(
        "first_charge_at", DateTime(True), nullable=False
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        "expires_at", DateTime(True), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        "created_at", DateTime(True), nullable=False
    )


class BillingPayment(Base):
    __tablename__ = "billing_payments"
    __table_args__ = {"schema": "public"}
    id: Mapped[str] = mapped_column("id", Text, primary_key=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(
        "provider_subscription_id", Text, nullable=True
    )
    checkout_id: Mapped[uuid.UUID] = mapped_column(
        "checkout_id", UUID(as_uuid=True), nullable=False
    )
    kind: Mapped[str] = mapped_column("kind", Text, nullable=False)
    status: Mapped[str] = mapped_column("status", Text, nullable=False)
    amount_paise: Mapped[int] = mapped_column("amount_paise", Integer, nullable=False)
    refunded_paise: Mapped[int] = mapped_column(
        "refunded_paise", Integer, nullable=False
    )
    currency: Mapped[str] = mapped_column("currency", Text, nullable=False)
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(
        "scheduled_at", DateTime(True), nullable=True
    )
    paid_at: Mapped[datetime.datetime | None] = mapped_column(
        "paid_at", DateTime(True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(
        "failure_reason", Text, nullable=True
    )
    receipt_number: Mapped[str | None] = mapped_column(
        "receipt_number", Text, nullable=True
    )
    invoice_number: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text(
            "('INV' || lpad(nextval('public.billing_invoice_number_seq')::text, 10, '0'))"
        ),
    )
    snapshot: Mapped[dict] = mapped_column("snapshot", JSONB, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        "updated_at", DateTime(True), nullable=False
    )


class BillingEvent(Base):
    __tablename__ = "billing_events"
    __table_args__ = {"schema": "public"}
    id: Mapped[str] = mapped_column("id", Text, primary_key=True, nullable=False)
    event_type: Mapped[str] = mapped_column("event_type", Text, nullable=False)
    provider_id: Mapped[str | None] = mapped_column("provider_id", Text, nullable=True)
    payload: Mapped[dict] = mapped_column("payload", JSONB, nullable=False)
    received_at: Mapped[datetime.datetime] = mapped_column(
        "received_at", DateTime(True), nullable=False
    )
    processed_at: Mapped[datetime.datetime | None] = mapped_column(
        "processed_at", DateTime(True), nullable=True
    )
    error: Mapped[str | None] = mapped_column("error", Text, nullable=True)


class BillingUsage(Base):
    __tablename__ = "billing_usage"
    __table_args__ = {"schema": "public"}
    id: Mapped[str] = mapped_column("id", Text, primary_key=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        "period_id", UUID(as_uuid=True), nullable=False
    )
    kind: Mapped[str] = mapped_column("kind", Text, nullable=False)
    status: Mapped[str] = mapped_column("status", Text, nullable=False)
    amount: Mapped[int] = mapped_column("amount", BigInteger, nullable=False)
    pack_id: Mapped[str | None] = mapped_column("pack_id", Text, nullable=True)
    response: Mapped[dict | None] = mapped_column("response", JSONB, nullable=True)
    cost: Mapped[dict] = mapped_column("cost", JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        "created_at", DateTime(True), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        "completed_at", DateTime(True), nullable=True
    )


class BillingCreditPack(Base):
    __tablename__ = "billing_credit_packs"
    __table_args__ = {"schema": "public"}
    id: Mapped[str] = mapped_column("id", Text, primary_key=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    replies: Mapped[int] = mapped_column("replies", Integer, nullable=False)
    used: Mapped[int] = mapped_column("used", Integer, nullable=False)
    reserved: Mapped[int] = mapped_column("reserved", Integer, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(
        "expires_at", DateTime(True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column("revoked", Boolean, nullable=False)


class BillingSourceUsage(Base):
    __tablename__ = "billing_source_usage"
    __table_args__ = {"schema": "public"}
    source_id: Mapped[uuid.UUID] = mapped_column(
        "source_id", UUID(as_uuid=True), primary_key=True, nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    tokens: Mapped[int] = mapped_column("tokens", BigInteger, nullable=False)
    pending_tokens: Mapped[int] = mapped_column(
        "pending_tokens", BigInteger, nullable=False
    )
    content_hash: Mapped[str | None] = mapped_column(
        "content_hash", Text, nullable=True
    )


class BillingNotice(Base):
    __tablename__ = "billing_notices"
    __table_args__ = {"schema": "public"}
    id: Mapped[str] = mapped_column("id", Text, primary_key=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(
        "organization_id", Text, nullable=False
    )
    kind: Mapped[str] = mapped_column("kind", Text, nullable=False)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(
        "sent_at", DateTime(True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        "created_at", DateTime(True), nullable=False
    )
