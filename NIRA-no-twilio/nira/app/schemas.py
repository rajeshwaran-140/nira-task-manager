import re
from datetime import datetime
from typing import Annotated, Literal, Optional
from pydantic import AfterValidator, BaseModel, EmailStr, Field, StringConstraints, field_validator

Category = Literal["emi", "bill", "exam", "assign", "appt", "work", "other"]
Repeat = Literal["none", "monthly", "yearly"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


def normalize_phone(raw: str) -> str:
    s = re.sub(r"[\s\-()]", "", raw or "")
    if re.fullmatch(r"[6-9]\d{9}", s):          # 10-digit Indian mobile
        s = "+91" + s
    elif re.fullmatch(r"0[6-9]\d{9}", s):
        s = "+91" + s[1:]
    elif re.fullmatch(r"91[6-9]\d{9}", s):
        s = "+" + s
    if not re.fullmatch(r"\+[1-9]\d{7,14}", s):
        raise ValueError("invalid phone number")
    return s


Phone = Annotated[str, AfterValidator(normalize_phone)]
Email = Annotated[EmailStr, AfterValidator(lambda e: e.lower())]


class PhoneIn(BaseModel):
    phone: Phone


class PhoneVerifyIn(PhoneIn):
    code: str = Field(pattern=r"^\d{6}$")
    name: Optional[str] = Field(default=None, max_length=80)


class RegisterIn(BaseModel):
    email: Email
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(default="", max_length=80)

    @field_validator("password")
    @classmethod
    def _bytes(cls, v):
        if len(v.encode()) > 72:
            raise ValueError("password too long")
        return v


class LoginIn(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=72)


class MagicRequestIn(BaseModel):
    email: Email


class MagicVerifyIn(BaseModel):
    token: str = Field(min_length=20, max_length=100)


class ProfileUpdate(BaseModel):
    name: str = Field(max_length=80)


class UserOut(BaseModel):
    id: int
    email: Optional[str]
    phone: Optional[str]
    phone_verified: bool
    name: str
    needs_phone: bool


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class DeadlineIn(BaseModel):
    title: Title
    category: Category = "other"
    due_at: datetime
    repeat: Repeat = "none"
    notes: str = Field(default="", max_length=1000)


class DeadlineUpdate(BaseModel):
    title: Optional[Title] = None
    category: Optional[Category] = None
    due_at: Optional[datetime] = None
    repeat: Optional[Repeat] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class DeadlineOut(BaseModel):
    id: int
    title: str
    category: str
    due_at: str          # ISO 8601 in the user's timezone, e.g. 2026-10-03T18:00+05:30
    repeat: str
    notes: str
    done: bool
    called: bool
    rolled: bool = False  # true when completing a repeating deadline moved it to the next cycle


class SettingsIn(BaseModel):
    lead_days: Optional[Literal[0, 1, 2]] = None
    call_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    language: Optional[Literal["ta", "en"]] = None
    timezone: Optional[str] = Field(default=None, max_length=40)
    calls_enabled: Optional[bool] = None
    consent_to_call: Optional[bool] = None
    speak_sensitive: Optional[bool] = None
    ai_voice_enabled: Optional[bool] = None


class SettingsOut(BaseModel):
    lead_days: int
    call_time: str
    language: str
    timezone: str
    calls_enabled: bool
    consent_to_call: bool
    speak_sensitive: bool
    ai_voice_enabled: bool
    phone: Optional[str]
    phone_verified: bool


# ---- AI key settings (Part 3A) ----
AIProviderName = Literal["gemini", "openai"]
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}")   # no "/" or "?": the model name goes into a URL path
_KEY_RE = re.compile(r"[\x21-\x7E]{8,512}")                     # printable ASCII, no spaces: it goes into an HTTP header


class AISettingsIn(BaseModel):
    provider: AIProviderName
    model: Optional[str] = Field(default=None, max_length=80)             # empty = provider default
    key: Optional[str] = Field(default=None, max_length=512, repr=False)  # empty = keep the saved key

    @field_validator("model")
    @classmethod
    def _model(cls, v):
        v = (v or "").strip()
        if not v:
            return None
        if not _MODEL_RE.fullmatch(v):
            raise ValueError("invalid model name")
        return v

    @field_validator("key")
    @classmethod
    def _key(cls, v):
        v = (v or "").strip()
        if not v:
            return None
        if not _KEY_RE.fullmatch(v):
            raise ValueError("invalid API key format")   # never echoes the value
        return v


class AISettingsOut(BaseModel):
    provider: AIProviderName
    model: str
    configured: bool                          # true when AI is usable: the user's own key OR the server default
    key_last4: Optional[str]                  # the user's own key only; the key itself is never returned
    source: Literal["user", "server", "none"]
    default_models: dict[str, str]


class AITestOut(BaseModel):
    ok: bool
    provider: AIProviderName
    model: str
    source: Literal["user", "server"]


# ---- Voice assistant ----
AssistantKind = Literal["add", "update", "complete", "delete"]


class AssistantCommandIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]   # what the browser heard


class AssistantActionOut(BaseModel):
    """A proposal waiting for the owner's confirmation. Nothing has been saved yet."""
    id: int
    kind: AssistantKind
    deadline_id: Optional[int]
    title: str                          # add: the new title; otherwise the current title of the deadline concerned
    changes: dict[str, str]             # add: every field; update: only what changes; complete/delete: {}. due_at is local time, e.g. 2026-10-02T18:00
    expires_in_seconds: int


class AssistantCommandOut(BaseModel):
    reply_text: str                     # 1-2 short spoken sentences, no markup
    action: Optional[AssistantActionOut]
    needs_more_info: bool               # true when the reply is a question the user should answer next


class AssistantConfirmOut(BaseModel):
    status: Literal["confirmed"]
    kind: AssistantKind
    deadline: Optional[DeadlineOut]     # None after a delete


class AssistantCancelOut(BaseModel):
    status: Literal["cancelled"]


class CallHistoryOut(BaseModel):
    id: int
    title: str
    phone: str            # masked, e.g. ••••••••3210
    call_time: str        # ISO 8601 in the user's timezone
    status: str           # queued | ringing | in-progress | completed | busy | failed | no-answer | canceled
    retry_count: int      # 0 = first call, 1 = first retry, ...
    duration_sec: int
