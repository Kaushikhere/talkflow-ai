from fastapi import APIRouter

from app import state
from app.models import MaintenanceRequest

router = APIRouter()


@router.get("/server-status")
def server_status():
    return {"maintenance_mode": state.maintenance_mode}


@router.post("/maintenance")
def set_maintenance_mode(payload: MaintenanceRequest):
    state.maintenance_mode = payload.enabled
    return {"maintenance_mode": state.maintenance_mode}
