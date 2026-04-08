"""Montagem de schemas de resposta a partir das entidades."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from typing import List, Optional

from bridge.models.entities import (
    Printer,
    PrinterAmsCache,
    PrinterStatusCache,
)
from bridge.schemas.api import (
    AmsResponse,
    AmsSlot,
    PrinterDetailResponse,
    PrinterListItem,
    PrinterStatusPanel,
)
from bridge.config import get_settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _effective_online(cache: Optional[PrinterStatusCache]) -> bool:
    if not cache:
        return False
    if cache.online:
        return True
    settings = get_settings()
    updated = _to_aware(cache.updated_at)
    recent = bool(updated and (_utc_now() - updated).total_seconds() <= settings.online_stale_seconds)
    has_live_signal = any(
        getattr(cache, k, None) is not None
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
    return recent and has_live_signal


def _parse_error_struct(cache: Optional[PrinterStatusCache]) -> dict:
    out = {
        "error_code": cache.error_code if cache else None,
        "error_message": cache.error_message if cache else None,
        "error_attr": None,
        "error_action": None,
        "error_timestamp": None,
        "error_raw": None,
    }
    if not cache:
        return out
    raw_msg = cache.error_message
    if not raw_msg:
        return out
    obj = None
    if isinstance(raw_msg, dict):
        obj = raw_msg
    elif isinstance(raw_msg, str):
        s = raw_msg.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = ast.literal_eval(s)
            except Exception:
                obj = None
    if isinstance(obj, dict):
        out["error_raw"] = obj
        if out["error_code"] is None and obj.get("code") is not None:
            out["error_code"] = str(obj.get("code"))
        if obj.get("attr") is not None:
            try:
                out["error_attr"] = int(obj.get("attr"))
            except Exception:
                pass
        if obj.get("action") is not None:
            try:
                out["error_action"] = int(obj.get("action"))
            except Exception:
                pass
        if obj.get("timestamp") is not None:
            try:
                out["error_timestamp"] = int(obj.get("timestamp"))
            except Exception:
                pass
        if obj.get("message"):
            out["error_message"] = str(obj.get("message"))
    return out


def _derive_state(cache: Optional[PrinterStatusCache]) -> Optional[str]:
    if not cache:
        return None
    if cache.state:
        return None
    if (cache.progress_percent or 0) > 0 and (cache.nozzle_temp or 0) > 0:
        return "printing"
    if _effective_online(cache):
        return "online"
    return "offline"


def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def list_item(p: Printer, cache: Optional[PrinterStatusCache]) -> PrinterListItem:
    return PrinterListItem(
        id=p.id,
        device_id=p.device_id,
        serial=p.serial,
        name=p.name or p.device_id,
        model=p.model,
        online=_effective_online(cache),
        last_update=cache.updated_at if cache else None,
    )


def ams_response(printer_id: int, ams: Optional[PrinterAmsCache]) -> AmsResponse:
    slots: List[AmsSlot] = []
    raw = (ams.raw_payload_json or {}) if ams else {}
    slot_list = raw.get("slots") if isinstance(raw, dict) else None
    if isinstance(slot_list, list):
        for s in slot_list:
            if isinstance(s, dict):
                slots.append(
                    AmsSlot(
                        slot=int(s.get("slot", 0)),
                        material=s.get("material"),
                        color=str(s["color"]) if s.get("color") is not None else None,
                        color_raw=str(s["color_raw"]) if s.get("color_raw") is not None else None,
                        color_hex=str(s["color_hex"]) if s.get("color_hex") is not None else None,
                        name=s.get("name"),
                        type=s.get("type"),
                        source_index=int(s["source_index"]) if s.get("source_index") is not None else None,
                    )
                )
    if not slots:
        slots = [
            AmsSlot(slot=i, material=None, color=None, name=None) for i in range(1, 5)
        ]
    quality_score = int(raw.get("quality_score", 0) or 0) if isinstance(raw, dict) else 0
    ams_status = "pending_refresh"
    if isinstance(raw, dict):
        ams_status = str(raw.get("ams_status") or "pending_refresh")
    last_good = raw.get("last_good_update_at") if isinstance(raw, dict) else None
    last_good_dt = None
    if isinstance(last_good, str):
        try:
            last_good_dt = datetime.fromisoformat(last_good.replace("Z", "+00:00"))
        except Exception:
            last_good_dt = None
    if ams_status != "pending_refresh" and last_good_dt is not None:
        settings = get_settings()
        lg = _to_aware(last_good_dt)
        if lg and (_utc_now() - lg).total_seconds() > settings.ams_cache_preserve_seconds:
            ams_status = "stale"
    return AmsResponse(
        printer_id=printer_id,
        has_ams=bool(raw.get("has_ams", ams.has_ams if ams else False)) if isinstance(raw, dict) else (ams.has_ams if ams else False),
        active_slot=ams.active_slot if ams else None,
        slots=slots,
        updated_at=ams.updated_at if ams else None,
        cache_preserved=bool(raw.get("cache_preserved", False)) if isinstance(raw, dict) else False,
        quality_score=quality_score,
        ams_status=ams_status,
        last_good_update_at=last_good_dt,
        context_job_name=raw.get("context_job_name") if isinstance(raw, dict) else None,
        context_task_id=raw.get("context_task_id") if isinstance(raw, dict) else None,
        data_source=raw.get("data_source") if isinstance(raw, dict) else None,
        ams_detected_struct=bool(raw.get("ams_detected_struct", False)) if isinstance(raw, dict) else False,
        external_spool_configured=bool(raw.get("external_spool_configured", False)) if isinstance(raw, dict) else False,
        filament_source=str(raw.get("filament_source") or "unknown") if isinstance(raw, dict) else "unknown",
        filament=raw.get("filament") if isinstance(raw, dict) and isinstance(raw.get("filament"), dict) else {
            "source": "unknown",
            "material": None,
            "color": None,
            "name": None,
            "type": None,
        },
        ams_capability_confirmed=bool(raw.get("ams_capability_confirmed", False)) if isinstance(raw, dict) else False,
        ams_capability_confidence=str(raw.get("ams_capability_confidence") or "low") if isinstance(raw, dict) else "low",
        ams_last_confirmed_at=_parse_iso_dt(raw.get("ams_last_confirmed_at")) if isinstance(raw, dict) else None,
        ams_last_confirmed_source=raw.get("ams_last_confirmed_source") if isinstance(raw, dict) else None,
    )


def detail_response(
    p: Printer,
    cache: Optional[PrinterStatusCache],
    ams: Optional[PrinterAmsCache],
) -> PrinterDetailResponse:
    ar = ams_response(p.id, ams)
    err = _parse_error_struct(cache)
    raw_hi = None
    if cache and cache.raw_payload_json and isinstance(cache.raw_payload_json, dict):
        if isinstance(cache.raw_payload_json.get("_highlights"), dict):
            raw_hi = dict(cache.raw_payload_json.get("_highlights"))
        print_d = cache.raw_payload_json.get("print")
        if isinstance(print_d, dict):
            raw_hi = {
                **(raw_hi or {}),
                "gcode_file": print_d.get("gcode_file"),
                "subtask_name": print_d.get("subtask_name"),
                "task_id": print_d.get("task_id"),
            }
    return PrinterDetailResponse(
        printer_id=p.id,
        device_id=p.device_id,
        serial=p.serial,
        name=p.name or p.device_id,
        model=p.model,
        online=_effective_online(cache),
        state=cache.state if cache else None,
        derived_state=_derive_state(cache),
        print_status=cache.print_status if cache else None,
        progress_percent=cache.progress_percent if cache else None,
        job_name=cache.job_name if cache else None,
        eta_minutes=cache.eta_minutes if cache else None,
        current_layer=cache.current_layer if cache else None,
        total_layers=cache.total_layers if cache else None,
        nozzle_temp=cache.nozzle_temp if cache else None,
        bed_temp=cache.bed_temp if cache else None,
        error_code=err["error_code"],
        error_message=err["error_message"],
        error_attr=err["error_attr"],
        error_action=err["error_action"],
        error_timestamp=err["error_timestamp"],
        error_raw=err["error_raw"],
        network_signal=cache.network_signal if cache else None,
        ams=ar,
        cache_preserved=ar.cache_preserved,
        quality_score=ar.quality_score,
        ams_status=ar.ams_status,
        last_good_update_at=ar.last_good_update_at,
        context_job_name=ar.context_job_name,
        context_task_id=ar.context_task_id,
        data_source=ar.data_source,
        ams_detected_struct=ar.ams_detected_struct,
        external_spool_configured=ar.external_spool_configured,
        filament_source=ar.filament_source,
        filament=ar.filament,
        ams_capability_confirmed=ar.ams_capability_confirmed,
        ams_capability_confidence=ar.ams_capability_confidence,
        ams_last_confirmed_at=ar.ams_last_confirmed_at,
        ams_last_confirmed_source=ar.ams_last_confirmed_source,
        last_update=cache.updated_at if cache else None,
        raw_highlights=raw_hi,
    )


def panel_response(
    p: Printer,
    cache: Optional[PrinterStatusCache],
) -> PrinterStatusPanel:
    err = _parse_error_struct(cache)
    return PrinterStatusPanel(
        printer_id=p.id,
        name=p.name or p.device_id,
        online=_effective_online(cache),
        state=cache.state if cache else None,
        derived_state=_derive_state(cache),
        print_status=cache.print_status if cache else None,
        progress_percent=cache.progress_percent if cache else None,
        job_name=cache.job_name if cache else None,
        eta_minutes=cache.eta_minutes if cache else None,
        current_layer=cache.current_layer if cache else None,
        total_layers=cache.total_layers if cache else None,
        nozzle_temp=cache.nozzle_temp if cache else None,
        bed_temp=cache.bed_temp if cache else None,
        error_code=err["error_code"],
        error_message=err["error_message"],
        error_attr=err["error_attr"],
        error_action=err["error_action"],
        error_timestamp=err["error_timestamp"],
        error_raw=err["error_raw"],
        last_seen=cache.updated_at if cache else None,
    )
