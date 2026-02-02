from __future__ import annotations

import datetime
import enum
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OtpType(str, enum.Enum):
    EMAIL = "EMAIL"
    MOBILE = "MOBILE"


class OtpPurpose(str, enum.Enum):
    LOGIN = "LOGIN"
    VERIFY_EMAIL = "VERIFY_EMAIL"
    VERIFY_PHONE = "VERIFY_PHONE"
    RESET_PASSWORD = "RESET_PASSWORD"


class Organizations(Base):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "public"}

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

    bots: Mapped[list["Bots"]] = relationship("Bots", back_populates="organization")
    organization_members: Mapped[list["OrganizationMembers"]] = relationship(
        "OrganizationMembers", back_populates="organization"
    )
    invites: Mapped[list["OrganizationInvites"]] = relationship(
        "OrganizationInvites", back_populates="organization"
    )
    api_keys: Mapped[list["ApiKeys"]] = relationship("ApiKeys", back_populates="organization")
    files: Mapped[list["Files"]] = relationship("Files", back_populates="organization")
    training_sources: Mapped[list["TrainingSources"]] = relationship(
        "TrainingSources", back_populates="organization"
    )
    conversations_meta: Mapped[list["ConversationsMeta"]] = relationship(
        "ConversationsMeta", back_populates="organization"
    )
    leads: Mapped[list["Leads"]] = relationship("Leads", back_populates="organization")


class Bots(Base):
    __tablename__ = "bots"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    capture_leads: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    tone: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    business_description: Mapped[str | None] = mapped_column(Text)
    first_message: Mapped[str | None] = mapped_column(Text)
    confirmation_message: Mapped[str | None] = mapped_column(Text)
    lead_capture_message: Mapped[str | None] = mapped_column(Text)
    lead_capture_timing: Mapped[str | None] = mapped_column(Text)
    capture_name: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    capture_email: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    capture_phone: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))

    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="bots"
    )
    api_keys: Mapped[list["ApiKeys"]] = relationship("ApiKeys", back_populates="bot")
    files: Mapped[list["Files"]] = relationship("Files", back_populates="bot")
    training_sources: Mapped[list["TrainingSources"]] = relationship(
        "TrainingSources", back_populates="bot"
    )
    conversations_meta: Mapped[list["ConversationsMeta"]] = relationship(
        "ConversationsMeta", back_populates="bot"
    )
    leads: Mapped[list["Leads"]] = relationship("Leads", back_populates="bot")


class Users(Base):
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
        DateTime(True), nullable=False, server_default=text("now()")
    )

    email: Mapped[str | None] = mapped_column(String, unique=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    email_verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    password_hash: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String)
    phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    phone_verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    full_name: Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(Text)

    google_id: Mapped[str | None] = mapped_column(Text, unique=True)
    github_id: Mapped[str | None] = mapped_column(Text, unique=True)
    microsoft_id: Mapped[str | None] = mapped_column(Text, unique=True)

    google_email: Mapped[str | None] = mapped_column(String)
    github_email: Mapped[str | None] = mapped_column(String)
    microsoft_email: Mapped[str | None] = mapped_column(String)

    last_logged_in: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    last_logged_in_ip: Mapped[str | None] = mapped_column(String)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    banned_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    organization_members: Mapped[list["OrganizationMembers"]] = relationship(
        "OrganizationMembers", back_populates="user"
    )
    otps: Mapped[list["Otps"]] = relationship("Otps", back_populates="user")


class OrganizationMembers(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        Index("organization_members_user_id_idx", "user_id"),
        Index("organization_members_organization_id_idx", "organization_id"),
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
    role: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.users.id", ondelete="CASCADE")
    )

    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="organization_members"
    )
    user: Mapped["Users | None"] = relationship("Users", back_populates="organization_members")


class OrganizationInvites(Base):
    __tablename__ = "organization_invites"
    __table_args__ = (
        Index("organization_invites_email_idx", "email"),
        Index("organization_invites_organization_id_idx", "organization_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'editor'"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    accepted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    organization: Mapped["Organizations"] = relationship("Organizations", back_populates="invites")


class Otps(Base):
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
    type: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)

    email: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)

    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    attempts: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        nullable=False, server_default=text("5")
    )
    ip_address: Mapped[str | None] = mapped_column(String)
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped["Users | None"] = relationship("Users", back_populates="otps")


class ApiKeys(Base):
    __tablename__ = "api_keys"
    __table_args__ = {"schema": "public"}

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

    bot: Mapped["Bots | None"] = relationship("Bots", back_populates="api_keys")
    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="api_keys"
    )
    conversations_meta: Mapped[list["ConversationsMeta"]] = relationship(
        "ConversationsMeta", back_populates="api_key"
    )


class Files(Base):
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

    bot: Mapped["Bots | None"] = relationship("Bots", back_populates="files")
    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="files"
    )


class TrainingSources(Base):
    __tablename__ = "training_sources"
    __table_args__ = {"schema": "public"}

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
    error_message: Mapped[str | None] = mapped_column(Text)
    source_value: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(Text)

    bot: Mapped["Bots | None"] = relationship("Bots", back_populates="training_sources")
    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="training_sources"
    )


class ConversationsMeta(Base):
    __tablename__ = "conversations_meta"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ai'"))
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="SET NULL")
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.api_keys.id", ondelete="SET NULL")
    )
    user_name: Mapped[str | None] = mapped_column(Text)
    user_email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    last_message_snippet: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

    api_key: Mapped["ApiKeys | None"] = relationship("ApiKeys", back_populates="conversations_meta")
    bot: Mapped["Bots | None"] = relationship("Bots", back_populates="conversations_meta")
    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="conversations_meta"
    )
    leads: Mapped[list["Leads"]] = relationship("Leads", back_populates="conversation")


class Leads(Base):
    __tablename__ = "leads"
    __table_args__ = {"schema": "public"}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("public.organizations.id", ondelete="CASCADE")
    )
    bot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.bots.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("public.conversations_meta.id", ondelete="SET NULL")
    )

    bot: Mapped["Bots | None"] = relationship("Bots", back_populates="leads")
    conversation: Mapped["ConversationsMeta | None"] = relationship(
        "ConversationsMeta", back_populates="leads"
    )
    organization: Mapped["Organizations | None"] = relationship(
        "Organizations", back_populates="leads"
    )

