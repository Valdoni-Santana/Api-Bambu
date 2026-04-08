"""Atualização periódica via cloud API quando MQTT está fraco ou ausente."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from bambulab.client import BambuAPIError

from bridge.config import get_settings
from bridge.models.db import get_session_factory
from bridge.models.entities import Printer, PrinterStatusCache
from bridge.services.bambu_runtime import get_runtime
from bridge.services.persistence import apply_status_to_db
from bridge.services.status_normalizer import normalize_mqtt_or_cloud_payload

logger = logging.getLogger(__name__)


def _merge_cloud_device(dev: Dict[str, Any], mqtt_extra: Dict[str, Any] | None) -> Dict[str, Any]:
    merged = dict(dev)
    if mqtt_extra:
        if isinstance(mqtt_extra.get("print"), dict):
            merged.setdefault("print", {})
            if isinstance(merged.get("print"), dict):
                for k, v in mqtt_extra["print"].items():
                    merged["print"].setdefault(k, v)
        for key in ("ams", "online"):
            if key in mqtt_extra and key not in merged:
                merged[key] = mqtt_extra[key]
    return merged


def poll_cloud_status_once() -> None:
    rt = get_runtime()
    if not rt.client:
        return
    settings = get_settings()
    factory = get_session_factory()
    session = factory()
    try:
        try:
            resp = rt.client.get_print_status(force=True)
        except BambuAPIError as e:
            logger.warning("Polling cloud print status falhou: %s", e)
            return

        devices = resp.get("devices") or []
        by_id = {d.get("dev_id"): d for d in devices if d.get("dev_id")}

        printers = session.execute(select(Printer).where(Printer.is_active.is_(True))).scalars().all()
        for p in printers:
            cloud = by_id.get(p.device_id)
            if not cloud:
                continue
            mqtt_data = rt.get_last_mqtt(p.device_id)
            stale = True
            cache = session.execute(
                select(PrinterStatusCache).where(PrinterStatusCache.printer_id == p.id)
            ).scalar_one_or_none()
            if cache and cache.updated_at:
                age = (datetime.now(timezone.utc) - cache.updated_at).total_seconds()
                stale = age > settings.mqtt_stale_seconds

            if mqtt_data and not stale:
                logger.debug("Polling ignorado (MQTT recente) para %s", p.device_id)
                continue

            merged = _merge_cloud_device(cloud, mqtt_data)
            norm = normalize_mqtt_or_cloud_payload(merged)
            apply_status_to_db(session, p.id, norm, append_history=False)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Erro no polling cloud")
    finally:
        session.close()
