from __future__ import annotations

import logging

from fastapi import APIRouter

from bridge.models.db import check_db_connection, get_session_factory
from bridge.schemas.api import HealthResponse
from bridge.services.bambu_runtime import get_runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    db_ok = "ok" if check_db_connection() else "fail"
    rt = get_runtime()
    auth = "ok" if rt.auth_ok else "fail"
    mqtt_n = rt.mqtt_connected_count()
    printers_count = 0
    try:
        factory = get_session_factory()
        s = factory()
        try:
            from sqlalchemy import func, select
            from bridge.models.entities import Printer

            printers_count = s.scalar(
                select(func.count()).select_from(Printer).where(Printer.is_active.is_(True))
            ) or 0
        finally:
            s.close()
    except Exception as e:
        logger.debug("health printers_count: %s", e)

    return HealthResponse(
        api="ok",
        db=db_ok,
        bambu_auth=auth,
        mqtt_connections=mqtt_n,
        printers_count=int(printers_count),
    )
