from fastapi import APIRouter, Depends, HTTPException, Request, status

from .auth import current_user
from .repository import DeviceRepository, DuplicateDeviceError
from .schemas import DeviceCreate, DeviceOut, DeviceUpdate


router = APIRouter(
    prefix="/api/devices",
    tags=["devices"],
    dependencies=[Depends(current_user)],
)


def _repository(request: Request) -> DeviceRepository:
    return request.app.state.devices


@router.get("", response_model=list[DeviceOut])
async def list_devices(request: Request) -> list[dict]:
    return await _repository(request).list_devices()


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(request: Request, payload: DeviceCreate) -> dict:
    try:
        return await _repository(request).create_device(payload.model_dump())
    except DuplicateDeviceError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A device with this name already exists.",
        )


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(request: Request, device_id: int) -> dict:
    device = await _repository(request).get_device(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")
    return device


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(request: Request, device_id: int, payload: DeviceUpdate) -> dict:
    data = payload.model_dump(exclude_unset=True)
    try:
        device = await _repository(request).update_device(device_id, data)
    except DuplicateDeviceError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A device with this name already exists.",
        )
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")
    return device


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(request: Request, device_id: int) -> None:
    deleted = await _repository(request).delete_device(device_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")
