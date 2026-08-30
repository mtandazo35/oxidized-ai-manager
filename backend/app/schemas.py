from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


DEVICE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
ADDRESS_PATTERN = r"^\S{1,255}$"
MODEL_PATTERN = r"^[a-z0-9_-]{1,64}$"


class DeviceCreate(BaseModel):
    name: str = Field(pattern=DEVICE_NAME_PATTERN)
    address: str = Field(pattern=ADDRESS_PATTERN)
    model: str = Field(default="routeros", pattern=MODEL_PATTERN)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, pattern=DEVICE_NAME_PATTERN)
    address: str | None = Field(default=None, pattern=ADDRESS_PATTERN)
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None


class DeviceOut(BaseModel):
    id: int
    name: str
    address: str
    model: str
    username: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class OxidizedNode(BaseModel):
    name: str
    ip: str
    model: str
    username: str
    password: str


class SettingsUpdate(BaseModel):
    backup_interval_minutes: int = Field(ge=5, le=10080)
    git_remote_enabled: bool
    git_remote_url: str = Field(default="", max_length=300)


class SettingsOut(BaseModel):
    backup_interval_minutes: int
    git_remote_enabled: bool
    git_remote_url: str
    last_push_ok: bool | None
    last_push_at: datetime | None
    last_push_detail: str


class BackupEventIn(BaseModel):
    node: str = Field(min_length=1, max_length=128)
    event: Literal["node_success", "node_fail"]
    commit: str = Field(default="", max_length=64)


class BackupEventOut(BaseModel):
    id: int
    node: str
    event: str
    commit_ref: str
    created_at: datetime


class BackupStatusOut(BaseModel):
    node: str
    last_event: str
    last_event_at: datetime
    last_success_at: datetime | None
    last_commit: str | None
