"""Versión desplegada y actualización desde Git (solo administrador)."""

from fastapi import APIRouter, Depends, HTTPException, status

from .auth import CurrentUser, require_admin
from .config import get_settings
from .schemas import UpdateRequestResult, VersionOut
from .updater import (
    UpdaterError,
    deployed_version,
    history,
    is_running,
    github_compare,
    github_repo,
    local_commit,
    read_status,
    remote_commit,
    request_update,
)


router = APIRouter(
    prefix="/api/system",
    tags=["system"],
    dependencies=[Depends(require_admin)],
)


@router.get("/version", response_model=VersionOut)
async def version() -> dict:
    """Commit desplegado y si hay uno más nuevo en el remoto."""
    settings = get_settings()
    result: dict = {
        "commit": "",
        "version": "",
        "short": "",
        "date": None,
        "subject": "",
        "remote_commit": "",
        "update_available": False,
        "error": "",
        "changes": [],
        "last_update": read_status(settings.update_channel_dir),
        "updating": is_running(settings.update_channel_dir),
    }
    try:
        result.update(await local_commit(settings.repo_git_dir))
        result["version"] = await deployed_version(settings.repo_git_dir)
    except UpdaterError as error:
        result["error"] = (
            f"No se pudo leer la versión desplegada: {error}. "
            "¿Está montado el repositorio en /repo/.git?"
        )
        return result
    # Con un remoto de GitHub, una sola consulta da la punta de la rama y la
    # lista de lo que falta. `git ls-remote` levanta un proceso y negocia con
    # el servidor: costaba varios segundos en cada pulsación.
    repo = await github_repo(settings.repo_git_dir)
    if repo:
        remote, cambios = await github_compare(
            repo, result["commit"], settings.update_branch
        )
        if remote:
            result["remote_commit"] = remote
            result["update_available"] = remote != result["commit"]
            result["changes"] = cambios
            return result
    try:
        remote = await remote_commit(
            settings.repo_git_dir, settings.update_branch
        )
    except UpdaterError as error:
        # Sin salida a internet se sigue informando la versión local: saber en
        # qué commit corre es útil aunque no se pueda comparar.
        result["error"] = f"No se pudo consultar el remoto: {error}"
        return result
    result["remote_commit"] = remote
    result["update_available"] = remote != result["commit"]
    return result


@router.get("/changelog")
async def changelog() -> dict:
    """Historial de cambios ya desplegados, del más nuevo al más viejo."""
    settings = get_settings()
    return {"changes": await history(settings.repo_git_dir)}


@router.post(
    "/update",
    response_model=UpdateRequestResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update(actor: CurrentUser = Depends(require_admin)) -> dict:
    """Solicita la actualización; la aplica el anfitrión, no el contenedor."""
    settings = get_settings()
    if is_running(settings.update_channel_dir):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una actualización en curso.",
        )
    try:
        request_update(settings.update_channel_dir, actor.username)
    except UpdaterError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        )
    return {
        "status": "solicitada",
        "detail": (
            "El anfitrión respaldará la plataforma y aplicará la actualización. "
            "El panel se reiniciará solo; espere unos segundos y recargue."
        ),
    }


@router.get("/update-status")
async def update_status() -> dict:
    settings = get_settings()
    return read_status(settings.update_channel_dir)
