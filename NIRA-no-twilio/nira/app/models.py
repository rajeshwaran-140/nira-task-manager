from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utcnow() -> datetime:
    """Naive UTC (all datetimes are stored as naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String(80), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump = sign out everywhere
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    settings: Mapped["UserSettings"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    deadlines: Mapped[list["Deadline"]] = relationship(cascade="all, delete-orphan")
    call_logs: Mapped[list["CallLog"]] = relationship(cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(cascade="all, delete-orphan")
    ai_settings: Mapped["UserAISettings"] = relationship(cascade="all, delete-orphan", uselist=False)
    pending_actions: Mapped[list["PendingAction"]] = relationship(cascade="all, delete-orphan")
    assistant_turns: Mapped[list["AssistantTurn"]] = relationship(cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lead_days: Mapped[int] = mapped_column(Integer, default=1)          # 0 same day / 1 / 2
    call_time: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM in user's timezone
    language: Mapped[str] = mapped_column(String(2), default="ta")      # ta | en
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Kolkata")
    calls_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_to_call: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    speak_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)  # opt-in: say amounts/account numbers aloud
    ai_voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # keypad option 3
    # Phase 3: secrets, encrypted with Fernet (app/security.py). Never returned by the API.
    ai_provider: Mapped[str] = mapped_column(String(10), default="gemini")
    ai_key_enc: Mapped[str | None] = mapped_column(Text)
    vobiz_auth_id_enc: Mapped[str | None] = mapped_column(Text)
    vobiz_auth_token_enc: Mapped[str | None] = mapped_column(Text)
    vobiz_caller_number: Mapped[str | None] = mapped_column(String(20))
    push_token: Mapped[str | None] = mapped_column(Text)  # Phase 5

    user: Mapped[User] = relationship(back_populates="settings")


class UserAISettings(Base):
    """A user's own AI provider key (Part 3A). The key is Fernet-encrypted; only the last 4 characters are kept
    in clear so the UI can show ••••1234. Never returned by any endpoint."""
    __tablename__ = "user_ai_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(10))    # gemini | openai
    model: Mapped[str] = mapped_column(String(80))
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_last4: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Deadline(Base):
    __tablename__ = "deadlines"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(10), default="other")
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    repeat: Mapped[str] = mapped_column(String(10), default="none")
    notes: Mapped[str] = mapped_column(Text, default="")
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # scheduler bookkeeping (Phase 4)
    call_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CallLog(Base):
    __tablename__ = "call_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deadline_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120), default="")
    provider_call_id: Mapped[str | None] = mapped_column(String(80), index=True)
    # Twilio's own words: queued / ringing / in-progress / completed / busy / failed / no-answer / canceled
    status: Mapped[str] = mapped_column(String(15), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=1)          # 1 = first call, 2 = first retry, ...
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    dtmf: Mapped[str | None] = mapped_column(String(10))  # what the user pressed
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str | None] = mapped_column(Text)  # JSON: tool calls / confirmation cards
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PendingAction(Base):
    """A change the voice assistant PROPOSED. Nothing in `deadlines` changes until the owner confirms it, once, before
    `expires_at` (10 minutes). payload = JSON {"deadline_id", "title", "fields"} with due_at as naive UTC, already validated."""
    __tablename__ = "pending_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))                       # add | update | complete | delete
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | confirmed | cancelled
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class AssistantTurn(Base):
    """One question + answer of the voice assistant, kept only so a follow-up ("6pm") can be understood.
    Only the newest 6 turns of the last 10 minutes are kept; older rows are deleted on every command."""
    __tablename__ = "assistant_turns"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user_text: Mapped[str] = mapped_column(Text, default="")
    assistant_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuthCode(Base):
    """One-time phone OTPs and email magic-link tokens. Only a keyed hash is stored."""
    __tablename__ = "auth_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(12), index=True)  # phone_login | phone_verify | magic
    target: Mapped[str] = mapped_column(String(254), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
