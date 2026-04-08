from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from bridge.api.deps import verify_api_token
from bridge.models.db import get_db
from bridge.models.entities import (
    Printer,
    PrinterAmsCache,
    PrinterSnapshot,
    PrinterStatusCache,
    PrinterStatusHistory,
)
from bridge.schemas.api import (
    AdvancedRefreshResponse,
    PrinterDetailResponse,
    PrinterListItem,
    PrinterStatusPanel,
    RefreshResponse,
    StatusHistoryItem,
)
from bridge.config import get_settings
from bridge.services.bambu_runtime import get_runtime
from bridge.services.camera_service import capture_and_store, get_latest_snapshot_row
from bridge.services.persistence import apply_status_to_db
from bridge.services.response_builders import (
    ams_response,
    detail_response,
    list_item,
    panel_response,
)
from bridge.services.status_normalizer import (
    normalize_mqtt_or_cloud_payload,
    normalize_mqtt_or_cloud_payload_with_debug,
)
from bridge.services.advanced_refresh import run_advanced_refresh, should_advanced_refresh

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_token)])


def _get_printer_or_404(session: Session, printer_id: int) -> Printer:
    p = session.get(Printer, printer_id)
    if not p or not p.is_active:
        raise HTTPException(status_code=404, detail="Impressora não encontrada.")
    return p


def _cache(session: Session, printer_id: int) -> PrinterStatusCache | None:
    return session.execute(
        select(PrinterStatusCache).where(PrinterStatusCache.printer_id == printer_id)
    ).scalar_one_or_none()


def _ams(session: Session, printer_id: int) -> PrinterAmsCache | None:
    return session.execute(
        select(PrinterAmsCache).where(PrinterAmsCache.printer_id == printer_id)
    ).scalar_one_or_none()


def _mask_sensitive(obj):
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
        return [_mask_sensitive(x) for x in obj]
    return obj


def _iso(dt):
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).isoformat()
    return str(dt)


@router.get("/printers", response_model=List[PrinterListItem])
def list_printers(session: Session = Depends(get_db)):
    printers = session.execute(select(Printer).where(Printer.is_active.is_(True))).scalars().all()
    out: List[PrinterListItem] = []
    for p in printers:
        c = _cache(session, p.id)
        out.append(list_item(p, c))
    return out


@router.get("/printers/{printer_id}", response_model=PrinterDetailResponse)
def printer_detail(
    printer_id: int,
    advanced_refresh: bool = False,
    session: Session = Depends(get_db),
):
    p = _get_printer_or_404(session, printer_id)
    settings = get_settings()
    c = _cache(session, p.id)
    do_adv = (advanced_refresh or settings.advanced_refresh_on_null_fields) and should_advanced_refresh(c)
    if settings.advanced_refresh_enabled and do_adv:
        try:
            run_advanced_refresh(session, p)
            session.commit()
            c = _cache(session, p.id)
        except Exception:
            session.rollback()
    return detail_response(p, c, _ams(session, p.id))


@router.get("/printers/{printer_id}/status", response_model=PrinterStatusPanel)
def printer_status(
    printer_id: int,
    advanced_refresh: bool = False,
    session: Session = Depends(get_db),
):
    p = _get_printer_or_404(session, printer_id)
    settings = get_settings()
    c = _cache(session, p.id)
    do_adv = (advanced_refresh or settings.advanced_refresh_on_null_fields) and should_advanced_refresh(c)
    if settings.advanced_refresh_enabled and do_adv:
        try:
            run_advanced_refresh(session, p)
            session.commit()
            c = _cache(session, p.id)
        except Exception:
            session.rollback()
    return panel_response(p, c)


@router.get("/printers/{printer_id}/ams")
def printer_ams(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    return ams_response(p.id, _ams(session, p.id)).model_dump()


@router.get("/printers/{printer_id}/debug/raw")
def printer_debug_raw(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    cache = _cache(session, p.id)
    ams = _ams(session, p.id)
    latest_hist = session.execute(
        select(PrinterStatusHistory)
        .where(PrinterStatusHistory.printer_id == p.id)
        .order_by(PrinterStatusHistory.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    status_raw = cache.raw_payload_json if cache else None
    if isinstance(status_raw, dict):
        highlights = status_raw.get("_highlights")
    else:
        highlights = None
    return {
        "printer_id": p.id,
        "device_id": p.device_id,
        "timestamps": {
            "status_updated_at": _iso(cache.updated_at if cache else None),
            "ams_updated_at": _iso(ams.updated_at if ams else None),
            "history_last_at": _iso(latest_hist.created_at if latest_hist else None),
        },
        "status_raw": _mask_sensitive(status_raw),
        "ams_raw": _mask_sensitive(ams.raw_payload_json if ams else None),
        "highlights": highlights or {},
    }


@router.get("/printers/{printer_id}/debug/normalized")
def printer_debug_normalized(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    cache = _cache(session, p.id)
    payload = cache.raw_payload_json if cache else None
    source = "status_cache.raw_payload_json"
    if isinstance(payload, dict):
        meta = payload.get("_meta")
        if isinstance(meta, dict) and meta.get("source_label"):
            source = str(meta.get("source_label"))
    if not payload:
        rt = get_runtime()
        payload = rt.get_last_mqtt(p.device_id)
        source = "mqtt passive"
    if not isinstance(payload, dict):
        raise HTTPException(status_code=404, detail="Sem payload bruto disponível para inspeção.")
    normalized = normalize_mqtt_or_cloud_payload_with_debug(payload)
    extracted = {
        "state": normalized.get("state"),
        "print_status": normalized.get("print_status"),
        "progress_percent": normalized.get("progress_percent"),
        "job_name": normalized.get("job_name"),
        "eta_minutes": normalized.get("eta_minutes"),
        "current_layer": normalized.get("current_layer"),
        "total_layers": normalized.get("total_layers"),
        "nozzle_temp": normalized.get("nozzle_temp"),
        "bed_temp": normalized.get("bed_temp"),
        "error_code": normalized.get("error_code"),
        "error_message": normalized.get("error_message"),
        "has_ams": (normalized.get("ams") or {}).get("has_ams"),
        "ams_detected_struct": (normalized.get("ams") or {}).get("ams_detected_struct"),
        "external_spool_configured": (normalized.get("ams") or {}).get("external_spool_configured"),
        "filament_source": (normalized.get("ams") or {}).get("filament_source"),
        "filament": (normalized.get("ams") or {}).get("filament"),
    }
    derived = {}
    if not extracted["state"] and (extracted["progress_percent"] or 0) > 0 and (
        extracted["nozzle_temp"] or 0
    ) > 0:
        derived["derived_state"] = "printing"
    field_sources = normalized.get("field_sources", {}) or {}
    if source in ("mqtt passive", "cloud poll") or str(source).startswith("advanced refresh"):
        prefixed = {}
        for k, v in field_sources.items():
            prefixed[k] = f"{source}:{v}" if v and ":" not in str(v) else v or source
        field_sources = prefixed
    if "derived_state" in derived:
        field_sources["derived_state"] = "derived"
    ams_cache = _ams(session, p.id)
    ams_cache_ctx = None
    if ams_cache and isinstance(ams_cache.raw_payload_json, dict):
        r = ams_cache.raw_payload_json
        ams_cache_ctx = {
            "has_ams": r.get("has_ams"),
            "quality_score": r.get("quality_score"),
            "ams_status": r.get("ams_status"),
            "cache_preserved": r.get("cache_preserved"),
            "ams_capability_confirmed": r.get("ams_capability_confirmed"),
            "ams_capability_confidence": r.get("ams_capability_confidence"),
            "ams_last_confirmed_at": r.get("ams_last_confirmed_at"),
            "ams_last_confirmed_source": r.get("ams_last_confirmed_source"),
            "context_job_name": r.get("context_job_name"),
            "context_task_id": r.get("context_task_id"),
        }

    return {
        "printer_id": p.id,
        "device_id": p.device_id,
        "raw_source": source,
        "payload_raw_summary": {
            "top_level_keys": sorted(list(payload.keys()))[:50],
            "has_print": isinstance(payload.get("print"), dict),
            "has_ams": "ams" in payload,
        },
        "extracted_fields": extracted,
        "ams_slots_raw_found": (
            ((normalized.get("ams") or {}).get("raw_payload_json") or {}).get("slots_raw_found")
            if isinstance((normalized.get("ams") or {}).get("raw_payload_json"), dict)
            else []
        ),
        "ams_slots_normalized": (normalized.get("ams") or {}).get("slots") or [],
        "inferred_fields": derived,
        "field_sources": field_sources,
        "ams_cache_context": ams_cache_ctx,
    }


@router.get("/printers/{printer_id}/camera/snapshot")
def printer_snapshot(
    printer_id: int,
    refresh: bool = False,
    session: Session = Depends(get_db),
):
    p = _get_printer_or_404(session, printer_id)
    if refresh:
        try:
            capture_and_store(session, printer_id)
            session.commit()
        except ValueError as e:
            session.rollback()
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:
            session.rollback()
            logger.exception("Snapshot refresh")
            raise HTTPException(status_code=503, detail=f"Falha ao capturar imagem: {e}") from e
    row = get_latest_snapshot_row(session, printer_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Nenhum snapshot disponível. Use refresh=true ou aguarde o job periódico.",
        )
    path = row.file_path
    return FileResponse(path, media_type=row.mime_type or "image/jpeg")


@router.get("/printers/{printer_id}/history", response_model=List[StatusHistoryItem])
def printer_history(
    printer_id: int,
    limit: int = 100,
    session: Session = Depends(get_db),
):
    _get_printer_or_404(session, printer_id)
    lim = max(1, min(limit, 500))
    rows = session.execute(
        select(PrinterStatusHistory)
        .where(PrinterStatusHistory.printer_id == printer_id)
        .order_by(PrinterStatusHistory.created_at.desc())
        .limit(lim)
    ).scalars().all()
    return [StatusHistoryItem.model_validate(r) for r in reversed(rows)]


@router.post("/printers/{printer_id}/refresh", response_model=RefreshResponse)
def printer_refresh(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    rt = get_runtime()
    merged = None
    mqtt_data = rt.get_last_mqtt(p.device_id)
    try:
        if rt.client:
            resp = rt.client.get_print_status(force=True)
            for d in resp.get("devices") or []:
                if d.get("dev_id") == p.device_id:
                    merged = dict(d)
                    break
    except Exception as e:
        logger.warning("refresh cloud falhou: %s", e)

    if merged is None and mqtt_data:
        merged = mqtt_data
    if merged is None:
        raise HTTPException(
            status_code=503,
            detail="Sem dados recentes da cloud nem MQTT para esta impressora.",
        )

    if mqtt_data:
        if isinstance(merged.get("print"), dict) and isinstance(mqtt_data.get("print"), dict):
            for k, v in mqtt_data["print"].items():
                merged.setdefault("print", {})
                merged["print"].setdefault(k, v)

    norm = normalize_mqtt_or_cloud_payload(merged)
    apply_status_to_db(session, p.id, norm, append_history=True)
    session.commit()

    pushed = rt.request_pushall(p.device_id)
    return RefreshResponse(
        printer_id=p.id,
        ok=True,
        message="Cache atualizado." + (" pushall MQTT enviado." if pushed else ""),
    )


@router.post("/printers/{printer_id}/refresh-advanced", response_model=AdvancedRefreshResponse)
def printer_refresh_advanced(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    result = run_advanced_refresh(session, p)
    session.commit()
    return AdvancedRefreshResponse(printer_id=p.id, **result)
