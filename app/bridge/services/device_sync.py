"""Sincronização de impressoras a partir da API cloud."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from bambulab.client import BambuAPIError

from bridge.models.entities import Printer
from bridge.services.bambu_runtime import get_runtime

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sync_devices_from_cloud(session: Session) -> int:
    rt = get_runtime()
    if not rt.client:
        raise RuntimeError("Cliente Bambu não inicializado")

    try:
        devices = rt.client.get_devices()
    except BambuAPIError as e:
        logger.error("get_devices falhou: %s", e)
        raise

    count = 0
    for d in devices:
        dev_id = d.get("dev_id") or d.get("serial")
        if not dev_id:
            continue
        row = session.execute(select(Printer).where(Printer.device_id == dev_id)).scalar_one_or_none()
        if row is None:
            row = Printer(device_id=dev_id)
            session.add(row)
            logger.info("Nova impressora registrada: %s", dev_id)
        row.serial = d.get("serial") or dev_id
        row.name = d.get("name") or row.name or dev_id
        row.model = d.get("dev_product_name") or d.get("dev_model_name") or row.model
        row.access_code = d.get("dev_access_code") or row.access_code
        row.is_active = True
        row.updated_at = utcnow()
        count += 1

    session.flush()
    return count


def list_active_device_ids(session: Session) -> List[str]:
    rows = session.execute(select(Printer.device_id).where(Printer.is_active.is_(True))).all()
    return [r[0] for r in rows]
