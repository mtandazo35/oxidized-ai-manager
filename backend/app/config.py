from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Oxidized AI Manager"
    app_version: str = "0.1.0"
    app_env: str = "development"
    app_secret_key: str
    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: str
    redis_host: str
    redis_port: int = 6379
    redis_password: str
    oxidized_url: str
    oxidized_source_token: str
    admin_username: str = "admin"
    admin_password: str = ""
    # La sesión caduca por INACTIVIDAD: el token dura esto y se renueva
    # mientras se use. Sin actividad, expira solo y el servidor deja de
    # aceptarlo, sin depender de que el navegador haga nada.
    access_token_ttl_minutes: int = 60
    session_idle_minutes: int = 60
    oxidized_backup_repo: str = "/oxidized-data/backups.git"
    # HSTS solo cuando el dominio y su certificado estén confirmados en NPM.
    app_enable_hsts: bool = False
    # Si no se recibe ningún evento de respaldo en este múltiplo del
    # intervalo configurado, /health/backups responde 503 (monitoreo externo).
    backup_staleness_factor: float = 3.0
    # Repositorio montado de solo lectura, para saber en qué commit corre.
    repo_git_dir: str = "/repo/.git"
    # Directorio compartido con el anfitrión: el backend deja ahí la petición
    # de actualización y lee el estado que escribe la unidad de systemd.
    update_channel_dir: str = "/update"
    update_branch: str = "main"
    # Bloqueos de acceso (persistentes en PostgreSQL).
    login_failure_window_minutes: int = 15
    account_lock_threshold: int = 8
    account_lock_minutes: int = 15
    ip_block_threshold: int = 20
    ip_block_minutes: int = 30
    # Cuánto se guarda la bitácora antes de purgarse sola.
    activity_retention_days: int = 90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
