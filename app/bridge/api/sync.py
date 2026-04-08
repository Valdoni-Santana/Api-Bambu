from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from bridge.api.deps import verify_api_token
from bridge.models.db import get_db
from bridge.schemas.api import SyncResponse
from bridge.services.bambu_runtime import get_runtime
from bridge.services.device_sync import list_active_device_ids, sync_devices_from_cloud

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_token)])


@router.post("/sync/devices", response_model=SyncResponse)
def sync_devices(session: Session = Depends(get_db)):
    n = sync_devices_from_cloud(session)
    session.commit()
    ids = list_active_device_ids(session)
    rt = get_runtime()
    rt.stop_mqtt()
    rt.start_mqtt_for_devices(ids)
    return SyncResponse(synced=n, message=f"{n} dispositivo(s) sincronizados; MQTT reiniciado.")
