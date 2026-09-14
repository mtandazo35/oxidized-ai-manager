import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# Patrón del nombre **ya normalizado**. Sigue siendo estricto porque ese
# nombre es el del archivo dentro de `backups.git` y viaja en las URLs de
# Oxidized (`/node/next/<nombre>`) y en los parámetros de consulta.
DEVICE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


def normalize_node_name(value: str) -> str:
    """Convierte lo que escriba una persona en un nombre de nodo válido.

    «Router Sucursal Norte» -> «Router-Sucursal-Norte»; los acentos se pierden.
    Se normaliza en el servidor en vez de rechazar la entrada: el operador
    escribe el nombre como lo tiene en la cabeza y la plataforma se encarga.
    """
    sin_acentos = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", value.strip())
        if unicodedata.category(caracter) != "Mn"
    )
    # Cualquier tramo de caracteres no utilizables (espacios, barras,
    # paréntesis...) se convierte en UN guion, no en uno por carácter.
    con_guiones = re.sub(r"[^A-Za-z0-9._-]+", "-", sin_acentos)
    limpio = re.sub(r"-{2,}", "-", con_guiones)
    limpio = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", limpio)
    return limpio[:128]


def _validate_node_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalizado = normalize_node_name(value)
    if not re.match(DEVICE_NAME_PATTERN, normalizado):
        raise ValueError(
            "debe empezar por una letra o un número y contener al menos un "
            "carácter utilizable"
        )
    return normalizado
ADDRESS_PATTERN = r"^\S{1,255}$"
MODEL_PATTERN = r"^[a-z0-9_-]{1,64}$"


class DeviceCreate(BaseModel):
    # Se acepta el nombre tal como lo escriba el operador y se normaliza aquí.
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(pattern=ADDRESS_PATTERN)
    port: int = Field(default=22, ge=1, le=65535)
    model: str = Field(default="routeros", pattern=MODEL_PATTERN)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    enabled: bool = True
    group_name: str = Field(default="", max_length=64)
    backup_interval_minutes: int = Field(default=0, ge=0, le=10080)

    _normaliza_nombre = field_validator("name")(_validate_node_name)


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, pattern=ADDRESS_PATTERN)
    port: int | None = Field(default=None, ge=1, le=65535)
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None
    group_name: str | None = Field(default=None, max_length=64)
    backup_interval_minutes: int | None = Field(default=None, ge=0, le=10080)

    _normaliza_nombre = field_validator("name")(_validate_node_name)


class DeviceOut(BaseModel):
    id: int
    name: str
    address: str
    port: int
    model: str
    username: str
    enabled: bool
    group_name: str = ""
    backup_interval_minutes: int = 0
    identity: str = ""
    ros_version: str = ""
    board: str = ""
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
    ssh_port: int
    model: str
    username: str
    password: str


class DeviceImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=262144)


class DeviceImportError(BaseModel):
    line: int
    message: str


class DeviceImportResult(BaseModel):
    created: int
    duplicates: list[str]
    errors: list[DeviceImportError]


class SettingsUpdate(BaseModel):
    backup_interval_minutes: int = Field(ge=5, le=10080)
    git_remote_enabled: bool
    git_remote_url: str = Field(default="", max_length=300)
    git_push_interval_minutes: int = Field(default=60, ge=5, le=10080)


class SettingsOut(BaseModel):
    backup_interval_minutes: int
    git_remote_enabled: bool
    git_remote_url: str
    git_push_interval_minutes: int
    last_push_ok: bool | None
    last_push_at: datetime | None
    last_push_detail: str


class BulkBackupRequest(BaseModel):
    scope: Literal["all", "group", "devices"]
    group: str = Field(default="", max_length=64)
    device_ids: list[int] = Field(default_factory=list, max_length=1000)


class BackupEventIn(BaseModel):
    node: str = Field(min_length=1, max_length=128)
    event: Literal["node_success", "node_fail", "post_store"]
    commit: str = Field(default="", max_length=64)


class BackupEventOut(BaseModel):
    id: int
    node: str
    event: str
    commit_ref: str
    created_at: datetime


class BackupStatusOut(BaseModel):
    node: str
    last_event: str | None
    last_event_at: datetime | None
    last_success_at: datetime | None
    last_commit: str | None


class AuditEvidence(BaseModel):
    detail: str
    line: int = 0


class AuditFinding(BaseModel):
    rule_id: str
    category: str
    severity: str
    title: str
    recommendation: str
    evidence: list[AuditEvidence]


class AuditReport(BaseModel):
    node: str
    commit: str
    ros_version: str = ""
    rules_evaluated: int
    counts: dict[str, int]
    score: int
    level: str
    findings: list[AuditFinding]


class AuditSummaryRow(BaseModel):
    node: str
    group_name: str = ""
    identity: str = ""
    ros_version: str = ""
    counts: dict[str, int]
    score: int
    level: str
    error: str = ""


ROLES = ("admin", "operador", "auditor", "lector")
Role = Literal["admin", "operador", "auditor", "lector"]
USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$"


class UserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=72)
    role: Role = "lector"
    # Empresa de la cuenta. Obligatoria salvo para `admin`, que ve todas; la
    # validación cruzada vive en la API porque depende del rol.
    group_name: str = Field(default="", max_length=64)
    must_change_password: bool = True


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=72)
    role: Role | None = None
    group_name: str | None = Field(default=None, max_length=64)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    group_name: str = ""
    must_change_password: bool = False
    created_at: datetime | None = None


class UpdateStatusOut(BaseModel):
    state: str = "sin-datos"
    detail: str = ""
    at: str | None = None
    # Commits entre los que se movió la última actualización aplicada.
    from_commit: str = Field(default="", alias="from")
    to_commit: str = Field(default="", alias="to")

    model_config = {"populate_by_name": True}


class VersionOut(BaseModel):
    commit: str = ""
    short: str = ""
    date: datetime | None = None
    subject: str = ""
    remote_commit: str = ""
    update_available: bool = False
    updating: bool = False
    error: str = ""
    last_update: dict = {}


class UpdateRequestResult(BaseModel):
    status: str
    detail: str


class ActivityEntry(BaseModel):
    id: int
    at: datetime
    username: str = ""
    ip: str = ""
    action: str
    target: str = ""
    detail: str = ""
    ok: bool = True


class IpBlockOut(BaseModel):
    ip: str
    blocked_until: datetime
    failures: int = 0
    reason: str = ""


class AccountLockOut(BaseModel):
    username: str
    locked_until: datetime
    failures: int = 0


class AccessPolicy(BaseModel):
    allowlist_enabled: bool = False
    allowlist: str = Field(default="", max_length=4000)
