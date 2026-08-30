from datetime import datetime

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


class OxidizedNode(BaseModel):
    name: str
    ip: str
    model: str
    username: str
    password: str
