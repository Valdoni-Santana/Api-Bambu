"""Persistência de cache e histórico no banco."""

from __future__ import annotations

import logging
from pathlib import Path
import json
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
    raw = ams_part.get("raw_payload_json")
    if isinstance(raw, dict):
        ams_root = raw.get("ams_root")
        if ams_root:
            return True
        raw_slots = raw.get("slots")
        if isinstance(raw_slots, list):
            for s in raw_slots:
                if isinstance(s, dict) and (
                    s.get("slot") not in (None, 0)
                    or s.get("material")
                    or s.get("color")
                    or s.get("name")
                    or s.get("type")
                ):
                    return True
    for s in ams_part.get("slots") or []:
        if isinstance(s, dict) and (
            s.get("material") or s.get("color") or s.get("name") or s.get("type")
        ):
            return True
    return False


def _to_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _score_ams(payload: Dict[str, Any]) -> int:
    score = 0
    if payload.get("has_ams"):
        score += 1
    if payload.get("active_slot") is not None:
        score += 2
    for s in payload.get("slots") or []:
        if not isinstance(s, dict):
            continue
        if s.get("material"):
            score += 1
        if s.get("color"):
            score += 1
        if s.get("name"):
            score += 1
        if s.get("type"):
            score += 1
    return score


def _normalize_color_value(raw: Any) -> tuple[Optional[str], Optional[str]]:
    if raw is None:
        return None, None
    s = str(raw).strip().lstrip("#").upper()
    if not s:
        return None, None
    if len(s) == 8:
        return s, f"#{s[:6]}"
    if len(s) == 6:
        return s, f"#{s}"
    return s, None


def _recover_slots_from_ams_root(ams_root: Dict[str, Any]) -> List[Dict[str, Any]]:
    units = ams_root.get("ams")
    if not isinstance(units, list) or not units:
        return []
    trays = units[0].get("tray") if isinstance(units[0], dict) else None
    if isinstance(trays, dict):
        trays = [trays]
    if not isinstance(trays, list):
        return []
    by_slot: Dict[int, Dict[str, Any]] = {}
    for t in trays:
        if not isinstance(t, dict):
            continue
        slot = _to_int_safe(t.get("id"))
        if slot is None:
            continue
        sidx = slot
        slot = slot + 1
        material = t.get("tray_type") or t.get("type") or t.get("tray_sub_brands")
        c_raw, c_hex = _normalize_color_value(t.get("tray_color") or (t.get("cols") or [None])[0])
        by_slot[slot] = {
            "slot": slot,
            "material": material,
            "color": c_raw,
            "color_raw": c_raw,
            "color_hex": c_hex,
            "name": t.get("tray_name") or t.get("tray_info_idx"),
            "type": t.get("tray_type") or t.get("type"),
            "source_index": sidx,
        }
    out = []
    for n in range(1, 5):
        out.append(
            by_slot.get(
                n,
                {
                    "slot": n,
                    "material": None,
                    "color": None,
                    "color_raw": None,
                    "color_hex": None,
                    "name": None,
                    "type": None,
                    "source_index": None,
                },
            )
        )
    return out


def _to_int_safe(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _has_real_ams_evidence(payload: Dict[str, Any]) -> bool:
    if payload.get("active_slot") is not None:
        return True
    for s in payload.get("slots") or []:
        if isinstance(s, dict) and (s.get("material") or s.get("color") or s.get("name") or s.get("type")):
            return True
    raw = payload.get("raw_payload_json") if isinstance(payload.get("raw_payload_json"), dict) else {}
    if raw.get("ams_root"):
        return True
    return False


def _new_print_context(old_ctx: Dict[str, Any], new_ctx: Dict[str, Any]) -> bool:
    old_job = (old_ctx.get("context_job_name") or "").strip()
    new_job = (new_ctx.get("context_job_name") or "").strip()
    old_task = (old_ctx.get("context_task_id") or "").strip()
    new_task = (new_ctx.get("context_task_id") or "").strip()
    if old_task and new_task and old_task != new_task:
        return True
    if old_job and new_job and old_job != new_job:
        return True
    old_progress = old_ctx.get("context_progress")
    new_progress = new_ctx.get("context_progress")
    try:
        if old_progress is not None and new_progress is not None:
            old_p = float(old_progress)
            new_p = float(new_progress)
            if old_p >= 40 and (old_p - new_p) >= 40:
                return True
    except Exception:
        pass
    return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mask_sensitive(obj: Any) -> Any:
    sensitive = {"token", "access_token", "password", "passwd", "authkey", "authorization"}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in sensitive:
                out[k] = "***"
            else:
                out[k] = _mask_sensitive(v)
        return out
    if isinstance(obj, list):
        return [_mask_sensitive(v) for v in obj]
    return obj


def _write_debug_sample(printer_id: int, normalized: Dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.debug_raw_payloads:
        return
    dbg_dir = Path.cwd() / "storage" / "debug"
    dbg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": utcnow().isoformat(),
        "printer_id": printer_id,
        "normalized": {k: v for k, v in normalized.items() if k not in ("raw_payload_json", "ams")},
        "raw_payload_json": _mask_sensitive(normalized.get("raw_payload_json")),
        "ams": _mask_sensitive(normalized.get("ams")),
    }
    out_file = dbg_dir / f"printer_{printer_id}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_status_to_db(
    session: Session,
    printer_id: int,
    normalized: Dict[str, Any],
    *,
    append_history: bool = True,
) -> None:
    _write_debug_sample(printer_id, normalized)
    ams_part = normalized.get("ams")

    row = session.execute(
        select(PrinterStatusCache).where(PrinterStatusCache.printer_id == printer_id)
    ).scalar_one_or_none()
    if row is None:
        row = PrinterStatusCache(printer_id=printer_id)
        session.add(row)

    # online: evitar degradar true -> false por payload curto/fraco.
    incoming_online = bool(normalized.get("online", False))
    has_activity_signal = any(
        normalized.get(k) is not None
        for k in (
            "state",
            "print_status",
            "progress_percent",
            "job_name",
            "current_layer",
            "total_layers",
            "nozzle_temp",
            "bed_temp",
            "network_signal",
        )
    )
    if incoming_online:
        row.online = True
    else:
        row.online = row.online if has_activity_signal else False
    # Não degrada cache com payload curto: mantém valor anterior se novo vier nulo.
    def keep_old_if_null(old, new):
        return old if new is None else new

    row.state = keep_old_if_null(row.state, normalized.get("state"))
    row.print_status = keep_old_if_null(row.print_status, normalized.get("print_status"))
    row.progress_percent = keep_old_if_null(row.progress_percent, normalized.get("progress_percent"))
    row.job_name = keep_old_if_null(row.job_name, normalized.get("job_name"))
    row.eta_minutes = keep_old_if_null(row.eta_minutes, normalized.get("eta_minutes"))
    row.current_layer = keep_old_if_null(row.current_layer, normalized.get("current_layer"))
    row.total_layers = keep_old_if_null(row.total_layers, normalized.get("total_layers"))
    row.nozzle_temp = keep_old_if_null(row.nozzle_temp, normalized.get("nozzle_temp"))
    row.bed_temp = keep_old_if_null(row.bed_temp, normalized.get("bed_temp"))
    row.error_code = keep_old_if_null(row.error_code, normalized.get("error_code"))
    row.error_message = keep_old_if_null(row.error_message, normalized.get("error_message"))
    row.network_signal = normalized.get("network_signal")
    raw_payload = normalized.get("raw_payload_json")
    if isinstance(raw_payload, dict):
        row.raw_payload_json = {
            **raw_payload,
            "_highlights": normalized.get("raw_highlights") or {},
            "_field_sources": normalized.get("field_sources") or {},
        }
    else:
        row.raw_payload_json = raw_payload
    row.updated_at = utcnow()

    if ams_part:
        source_label = None
        if isinstance(raw_payload, dict):
            meta = raw_payload.get("_meta")
            if isinstance(meta, dict):
                source_label = meta.get("source_label")
        context = {
            "context_job_name": normalized.get("job_name"),
            "context_task_id": (normalized.get("raw_highlights") or {}).get("task_id"),
            "context_print_status": normalized.get("print_status"),
            "context_progress": normalized.get("progress_percent"),
            "data_source": source_label or "unknown",
        }
        apply_ams_to_db(session, printer_id, ams_part, context)

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


def apply_ams_to_db(
    session: Session,
    printer_id: int,
    ams_part: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    settings = get_settings()
    now = utcnow()
    row = session.execute(
        select(PrinterAmsCache).where(PrinterAmsCache.printer_id == printer_id)
    ).scalar_one_or_none()
    if row is None:
        row = PrinterAmsCache(printer_id=printer_id)
        session.add(row)
    old_raw = row.raw_payload_json if isinstance(row.raw_payload_json, dict) else {}
    old_slots = old_raw.get("slots") if isinstance(old_raw.get("slots"), list) else []
    old_quality = int(old_raw.get("quality_score", 0) or 0)
    old_good_at = old_raw.get("last_good_update_at")
    old_capability_confirmed = bool(old_raw.get("ams_capability_confirmed", False))
    old_absent_count = int(old_raw.get("ams_absent_count", 0) or 0)

    new_payload = {
        "has_ams": bool(ams_part.get("has_ams")),
        "ams_detected_struct": bool(ams_part.get("ams_detected_struct")),
        "external_spool_configured": bool(ams_part.get("external_spool_configured")),
        "filament_source": ams_part.get("filament_source") or "unknown",
        "filament": ams_part.get("filament") or {},
        "active_slot": ams_part.get("active_slot"),
        "slots": ams_part.get("slots", []),
        "raw_payload_json": ams_part.get("raw_payload_json") if isinstance(ams_part.get("raw_payload_json"), dict) else {},
    }
    new_quality = _score_ams(new_payload)
    new_has_real_ams = _has_real_ams_evidence(new_payload)
    capability_confirmed = old_capability_confirmed or new_has_real_ams
    old_ctx = {
        "context_job_name": old_raw.get("context_job_name"),
        "context_task_id": old_raw.get("context_task_id"),
        "context_progress": old_raw.get("context_progress"),
    }
    context_changed = _new_print_context(old_ctx, context)

    old_good_dt = None
    if isinstance(old_good_at, str):
        try:
            old_good_dt = _to_aware(datetime.fromisoformat(old_good_at))
        except Exception:
            old_good_dt = None
    if old_good_dt is None:
        old_good_dt = _to_aware(row.updated_at)
    age_sec = None
    if old_good_dt:
        age_sec = (now - old_good_dt).total_seconds()
    within_ttl = age_sec is not None and age_sec <= settings.ams_cache_preserve_seconds

    should_preserve = False
    if capability_confirmed and old_quality > 0 and new_quality < old_quality:
        if settings.ams_context_strict:
            same_ctx = (
                (context.get("context_task_id") and context.get("context_task_id") == old_ctx.get("context_task_id"))
                or (context.get("context_job_name") and context.get("context_job_name") == old_ctx.get("context_job_name"))
            )
            should_preserve = same_ctx or (not context_changed and within_ttl)
        else:
            should_preserve = not context_changed and within_ttl
    if capability_confirmed and new_quality == 0 and within_ttl and not context_changed:
        should_preserve = True

    absent_count = 0
    if capability_confirmed:
        absent_count = 0 if new_has_real_ams else (old_absent_count + 1)
    invalidate_confirmed = bool(
        capability_confirmed
        and (context_changed or not within_ttl)
        and absent_count >= 3
        and not new_has_real_ams
    )
    if invalidate_confirmed:
        capability_confirmed = False

    if should_preserve:
        final_slots = old_slots
        final_quality = old_quality
        cache_preserved = True
        ams_status = "preserved"
        last_good_update_at = old_raw.get("last_good_update_at") or now.isoformat()
        has_ams = bool(old_raw.get("has_ams", row.has_ams))
        ams_detected_struct = bool(old_raw.get("ams_detected_struct", False))
        external_spool_configured = bool(old_raw.get("external_spool_configured", False))
        filament_source = str(old_raw.get("filament_source") or "unknown")
        filament = old_raw.get("filament") or {}
        active_slot = old_raw.get("active_slot", row.active_slot)
        # Se houver snapshot bruto histórico melhor (ams_root), recuperar slots mais completos.
        old_best = old_raw.get("ams_last_good_payload")
        if isinstance(old_best, dict):
            root = old_best.get("raw_payload_json")
            ams_root = root.get("ams_root") if isinstance(root, dict) else None
            if isinstance(ams_root, dict):
                recovered = _recover_slots_from_ams_root(ams_root)
                recovered_score = _score_ams(
                    {"has_ams": True, "active_slot": active_slot, "slots": recovered}
                )
                if recovered_score > final_quality:
                    final_slots = recovered
                    final_quality = recovered_score
    else:
        final_slots = new_payload["slots"]
        final_quality = new_quality
        cache_preserved = False
        has_ams = bool((new_payload["has_ams"] and new_has_real_ams) or capability_confirmed)
        ams_detected_struct = bool(new_payload.get("ams_detected_struct"))
        external_spool_configured = bool(new_payload.get("external_spool_configured"))
        filament_source = str(new_payload.get("filament_source") or "unknown")
        filament = new_payload.get("filament") or {}
        active_slot = new_payload["active_slot"]
        if context_changed and new_quality == 0:
            ams_status = "pending_refresh"
            final_slots = [
                {"slot": i, "material": None, "color": None, "name": None, "type": None}
                for i in range(1, 5)
            ]
        elif new_quality < old_quality:
            ams_status = "pending_refresh"
        elif new_quality > old_quality:
            ams_status = "fresh"
        elif new_quality == old_quality and new_quality > 0:
            ams_status = "fresh"
        elif new_quality == 0 and old_quality == 0:
            ams_status = "pending_refresh"
        else:
            ams_status = "fresh"
        last_good_update_at = now.isoformat() if final_quality > 0 else (old_raw.get("last_good_update_at"))
        if capability_confirmed and new_quality == 0 and not context_changed and within_ttl:
            ams_status = "preserved"

    # stale override
    if last_good_update_at:
        try:
            lg = _to_aware(datetime.fromisoformat(last_good_update_at))
            if lg and (now - lg).total_seconds() > settings.ams_cache_preserve_seconds:
                ams_status = "stale"
        except Exception:
            pass

    if capability_confirmed and not has_ams and within_ttl and not context_changed:
        has_ams = True
    if invalidate_confirmed:
        has_ams = False

    if new_has_real_ams:
        ams_last_confirmed_at = now.isoformat()
        ams_last_confirmed_source = context.get("data_source")
        ams_last_good_payload = {
            "active_slot": new_payload.get("active_slot"),
            "slots": new_payload.get("slots"),
            "raw_payload_json": new_payload.get("raw_payload_json"),
        }
    else:
        ams_last_confirmed_at = old_raw.get("ams_last_confirmed_at")
        ams_last_confirmed_source = old_raw.get("ams_last_confirmed_source")
        ams_last_good_payload = old_raw.get("ams_last_good_payload")

    if not capability_confirmed:
        capability_confidence = "low"
    elif new_has_real_ams:
        capability_confidence = "high"
    else:
        capability_confidence = "medium"

    row.has_ams = has_ams
    row.active_slot = active_slot
    row.raw_payload_json = {
        "ams_root": (ams_part.get("raw_payload_json") or {}).get("ams_root")
        if isinstance(ams_part.get("raw_payload_json"), dict)
        else None,
        "slots": final_slots,
        "has_ams": has_ams,
        "ams_detected_struct": ams_detected_struct,
        "external_spool_configured": external_spool_configured,
        "filament_source": filament_source,
        "filament": filament,
        "quality_score": int(final_quality),
        "cache_preserved": bool(cache_preserved),
        "ams_status": ams_status,
        "ams_capability_confirmed": bool(capability_confirmed),
        "ams_capability_confidence": capability_confidence,
        "ams_last_confirmed_at": ams_last_confirmed_at,
        "ams_last_confirmed_source": ams_last_confirmed_source,
        "ams_last_good_payload": ams_last_good_payload,
        "ams_absent_count": int(absent_count),
        "last_good_update_at": last_good_update_at,
        "context_job_name": context.get("context_job_name"),
        "context_task_id": context.get("context_task_id"),
        "context_print_status": context.get("context_print_status"),
        "context_progress": context.get("context_progress"),
        "data_source": context.get("data_source"),
    }
    row.updated_at = now


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
