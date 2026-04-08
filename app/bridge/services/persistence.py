"""Persistência de cache e histórico no banco."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from bridge.config import get_settings
from bridge.models.entities import (
    PrinterAmsCache,
    PrinterStatusCache,
    PrinterStatusHistory,
)

logger = logging.getLogger(__name__)


def _ams_is_non_trivial(ams_part: Dict[str, Any]) -> bool:
    if ams_part.get("has_ams"):
        return True
    if ams_part.get("raw_payload_json"):
        return True
    for s in ams_part.get("slots") or []:
        if isinstance(s, dict) and (s.get("material") or s.get("color") or s.get("name")):
            return True
    return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def apply_status_to_db(
    session: Session,
    printer_id: int,
    normalized: Dict[str, Any],
    *,
    append_history: bool = True,
) -> None:
    ams_part = normalized.pop("ams", None)

    row = session.execute(
        select(PrinterStatusCache).where(PrinterStatusCache.printer_id == printer_id)
    ).scalar_one_or_none()
    if row is None:
        row = PrinterStatusCache(printer_id=printer_id)
        session.add(row)

    row.online = normalized.get("online", False)
    row.state = normalized.get("state")
    row.print_status = normalized.get("print_status")
    row.progress_percent = normalized.get("progress_percent")
    row.job_name = normalized.get("job_name")
    row.eta_minutes = normalized.get("eta_minutes")
    row.current_layer = normalized.get("current_layer")
    row.total_layers = normalized.get("total_layers")
    row.nozzle_temp = normalized.get("nozzle_temp")
    row.bed_temp = normalized.get("bed_temp")
    row.error_code = normalized.get("error_code")
    row.error_message = normalized.get("error_message")
    row.network_signal = normalized.get("network_signal")
    row.raw_payload_json = normalized.get("raw_payload_json")
    row.updated_at = utcnow()

    if ams_part and _ams_is_non_trivial(ams_part):
        apply_ams_to_db(session, printer_id, ams_part)

    if append_history:
        hist = PrinterStatusHistory(
            printer_id=printer_id,
            state=row.state,
            print_status=row.print_status,
            progress_percent=row.progress_percent,
            job_name=row.job_name,
            nozzle_temp=row.nozzle_temp,
            bed_temp=row.bed_temp,
            raw_payload_json=row.raw_payload_json,
            created_at=utcnow(),
        )
        session.add(hist)
        _trim_history(session, printer_id)


def apply_ams_to_db(session: Session, printer_id: int, ams_part: Dict[str, Any]) -> None:
    row = session.execute(
        select(PrinterAmsCache).where(PrinterAmsCache.printer_id == printer_id)
    ).scalar_one_or_none()
    if row is None:
        row = PrinterAmsCache(printer_id=printer_id)
        session.add(row)
    row.has_ams = bool(ams_part.get("has_ams"))
    row.active_slot = ams_part.get("active_slot")
    row.raw_payload_json = ams_part.get("raw_payload_json") or {
        "slots": ams_part.get("slots", [])
    }
    row.updated_at = utcnow()


def _trim_history(session: Session, printer_id: int) -> None:
    settings = get_settings()
    limit = settings.history_max_per_printer
    keep_ids = list(
        session.execute(
            select(PrinterStatusHistory.id)
            .where(PrinterStatusHistory.printer_id == printer_id)
            .order_by(PrinterStatusHistory.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    if not keep_ids:
        return
    session.execute(
        delete(PrinterStatusHistory).where(
            PrinterStatusHistory.printer_id == printer_id,
            PrinterStatusHistory.id.not_in(keep_ids),
        )
    )
