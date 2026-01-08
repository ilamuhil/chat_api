from __future__ import annotations

"""
Supabase Postgres ORM models.

This file contains the `sqlacodegen` output (with a small tweak to avoid
Profiles inheriting from Users).
"""

from typing import Optional
import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Users(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email_change_confirm_status >= 0 AND email_change_confirm_status <= 2",
            name="users_email_change_confirm_status_check",
        ),
        PrimaryKeyConstraint("id", name="users_pkey"),
        UniqueConstraint("phone", name="users_phone_key"),
        Index("confirmation_token_idx", "confirmation_token", unique=True),
        Index("email_change_token_current_idx", "email_change_token_current", unique=True),
        Index("email_change_token_new_idx", "email_change_token_new", unique=True),
        Index("reauthentication_token_idx", "reauthentication_token", unique=True),
        Index("recovery_token_idx", "recovery_token", unique=True),
        Index("users_email_partial_key", "email", unique=True),
        Index("users_instance_id_email_idx", "instance_id"),
        Index("users_instance_id_idx", "instance_id"),
        Index("users_is_anonymous_idx", "is_anonymous"),
        {
            "comment": "Auth: Stores user login data within a secure schema.",
            "schema": "auth",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    is_sso_user: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="Auth: Set this column to true when the account comes from SSO. These accounts can have duplicate emails.",
    )
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    aud: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    encrypted_password: Mapped[Optional[str]] = mapped_column(String(255))
    email_confirmed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    invited_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    confirmation_token: Mapped[Optional[str]] = mapped_column(String(255))
    confirmation_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    recovery_token: Mapped[Optional[str]] = mapped_column(String(255))
    recovery_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    email_change_token_new: Mapped[Optional[str]] = mapped_column(String(255))
    email_change: Mapped[Optional[str]] = mapped_column(String(255))
    email_change_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_sign_in_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    raw_app_meta_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    raw_user_meta_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    is_super_admin: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    phone: Mapped[Optional[str]] = mapped_column(Text, server_default=text("NULL::character varying"))
    phone_confirmed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    phone_change: Mapped[Optional[str]] = mapped_column(Text, server_default=text("''::character varying"))
    phone_change_token: Mapped[Optional[str]] = mapped_column(
        String(255), server_default=text("''::character varying")
    )
    phone_change_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    confirmed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), Computed("LEAST(email_confirmed_at, phone_confirmed_at)", persisted=True)
    )
    email_change_token_current: Mapped[Optional[str]] = mapped_column(
        String(255), server_default=text("''::character varying")
    )
    email_change_confirm_status: Mapped[Optional[int]] = mapped_column(SmallInteger, server_default=text("0"))
    banned_until: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    reauthentication_token: Mapped[Optional[str]] = mapped_column(
        String(255), server_default=text("''::character varying")
    )
    reauthentication_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    organization_members: Mapped[list["OrganizationMembers"]] = relationship(
        "OrganizationMembers", back_populates="user"
    )
    profile: Mapped[Optional["Profiles"]] = relationship(
        "Profiles", back_populates="user", uselist=False
    )


class Organizations(Base):
    __tablename__ = "organizations"
    __table_args__ = (PrimaryKeyConstraint("id", name="organizations_pkey"), {"schema": "public"})

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    name: Mapped[Optional[str]] = mapped_column(Text)
    logo_url: Mapped[Optional[str]] = mapped_column(Text)
    address: Mapped[Optional[dict]] = mapped_column(JSONB)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    email_token: Mapped[Optional[str]] = mapped_column(Text)

    bots: Mapped[list["Bots"]] = relationship("Bots", back_populates="organization")
    organization_members: Mapped[list["OrganizationMembers"]] = relationship(
        "OrganizationMembers", back_populates="organization"
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
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="bots_organization_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="bots_pkey"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(
            start=1,
            increment=1,
            minvalue=1,
            maxvalue=9223372036854775807,
            cycle=False,
            cache=1,
        ),
        primary_key=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    capture_leads: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String)
    tone: Mapped[Optional[str]] = mapped_column(Text)
    role: Mapped[Optional[str]] = mapped_column(Text)
    business_description: Mapped[Optional[str]] = mapped_column(Text)
    first_message: Mapped[Optional[str]] = mapped_column(Text)
    confirmation_message: Mapped[Optional[str]] = mapped_column(Text)
    lead_capture_message: Mapped[Optional[str]] = mapped_column(Text)
    lead_capture_timing: Mapped[Optional[str]] = mapped_column(Text)
    capture_name: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=text("false"))
    capture_email: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=text("false"))
    capture_phone: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=text("false"))

    organization: Mapped[Optional["Organizations"]] = relationship("Organizations", back_populates="bots")
    api_keys: Mapped[list["ApiKeys"]] = relationship("ApiKeys", back_populates="bot")
    files: Mapped[list["Files"]] = relationship("Files", back_populates="bot")
    training_sources: Mapped[list["TrainingSources"]] = relationship(
        "TrainingSources", back_populates="bot"
    )
    conversations_meta: Mapped[list["ConversationsMeta"]] = relationship(
        "ConversationsMeta", back_populates="bot"
    )
    leads: Mapped[list["Leads"]] = relationship("Leads", back_populates="bot")


class OrganizationMembers(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="organization_members_organization_id_fkey",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["auth.users.id"],
            ondelete="CASCADE",
            name="organization_members_user_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="organization_members_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String, server_default=text("''::character varying"))
    role: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    organization: Mapped[Optional["Organizations"]] = relationship(
        "Organizations", back_populates="organization_members"
    )
    user: Mapped[Optional["Users"]] = relationship("Users", back_populates="organization_members")


class Profiles(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        ForeignKeyConstraint(["id"], ["auth.users.id"], ondelete="CASCADE", name="profiles_id_fkey"),
        PrimaryKeyConstraint("id", name="profiles_pkey"),
        {"comment": "Contains user data", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    full_name: Mapped[Optional[str]] = mapped_column(String)
    phone: Mapped[Optional[str]] = mapped_column(String)

    user: Mapped["Users"] = relationship("Users", back_populates="profile", uselist=False)


class ApiKeys(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(["bot_id"], ["public.bots.id"], ondelete="CASCADE", name="api_keys_bot_id_fkey"),
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="api_keys_organization_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="api_keys_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=text("true"))
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )

    bot: Mapped[Optional["Bots"]] = relationship("Bots", back_populates="api_keys")
    organization: Mapped[Optional["Organizations"]] = relationship("Organizations", back_populates="api_keys")
    conversations_meta: Mapped[list["ConversationsMeta"]] = relationship(
        "ConversationsMeta", back_populates="api_key"
    )


class Files(Base):
    __tablename__ = "files"
    __table_args__ = (
        ForeignKeyConstraint(["bot_id"], ["public.bots.id"], ondelete="CASCADE", name="files_bot_id_fkey"),
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="files_organization_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="files_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    provider: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'supabase'::text"))
    bucket: Mapped[Optional[str]] = mapped_column(Text)
    path: Mapped[Optional[str]] = mapped_column(Text)
    original_filename: Mapped[Optional[str]] = mapped_column(Text)
    mime_type: Mapped[Optional[str]] = mapped_column(Text)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    purpose: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)

    bot: Mapped[Optional["Bots"]] = relationship("Bots", back_populates="files")
    organization: Mapped[Optional["Organizations"]] = relationship("Organizations", back_populates="files")


class TrainingSources(Base):
    __tablename__ = "training_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bot_id"], ["public.bots.id"], ondelete="CASCADE", name="training_sources_bot_id_fkey"
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="training_sources_organization_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="training_sources_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    organization_id: Mapped[Optional[str]] = mapped_column(String)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    type: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    source_value: Mapped[str] = mapped_column(Text)
    bot: Mapped[Optional["Bots"]] = relationship("Bots", back_populates="training_sources")
    organization: Mapped[Optional["Organizations"]] = relationship(
        "Organizations", back_populates="training_sources"
    )


class ConversationsMeta(Base):
    __tablename__ = "conversations_meta"
    __table_args__ = (
        ForeignKeyConstraint(
            ["api_key_id"],
            ["public.api_keys.id"],
            ondelete="SET NULL",
            name="conversations_meta_api_key_id_fkey",
        ),
        ForeignKeyConstraint(
            ["bot_id"],
            ["public.bots.id"],
            ondelete="SET NULL",
            name="conversations_meta_bot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="conversations_meta_organization_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="conversations_meta_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ai'::text"))
    organization_id: Mapped[Optional[str]] = mapped_column(String)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    user_name: Mapped[Optional[str]] = mapped_column(Text)
    user_email: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    last_message_snippet: Mapped[Optional[str]] = mapped_column(Text)
    last_message_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    api_key: Mapped[Optional["ApiKeys"]] = relationship("ApiKeys", back_populates="conversations_meta")
    bot: Mapped[Optional["Bots"]] = relationship("Bots", back_populates="conversations_meta")
    organization: Mapped[Optional["Organizations"]] = relationship(
        "Organizations", back_populates="conversations_meta"
    )
    leads: Mapped[list["Leads"]] = relationship("Leads", back_populates="conversation")


class Leads(Base):
    __tablename__ = "leads"
    __table_args__ = (
        ForeignKeyConstraint(["bot_id"], ["public.bots.id"], ondelete="SET NULL", name="leads_bot_id_fkey"),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["public.conversations_meta.id"],
            ondelete="SET NULL",
            name="leads_conversation_id_fkey",
        ),
        ForeignKeyConstraint(
            ["organization_id"],
            ["public.organizations.id"],
            ondelete="CASCADE",
            name="leads_organization_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="leads_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    name: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    organization_id: Mapped[Optional[str]] = mapped_column(String)
    bot_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    bot: Mapped[Optional["Bots"]] = relationship("Bots", back_populates="leads")
    conversation: Mapped[Optional["ConversationsMeta"]] = relationship(
        "ConversationsMeta", back_populates="leads"
    )
    organization: Mapped[Optional["Organizations"]] = relationship("Organizations", back_populates="leads")


