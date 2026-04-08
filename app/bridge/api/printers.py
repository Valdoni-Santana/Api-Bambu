from __future__ import annotations

import logging
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
    PrinterDetailResponse,
    PrinterListItem,
    PrinterStatusPanel,
    RefreshResponse,
    StatusHistoryItem,
)
from bridge.services.bambu_runtime import get_runtime
from bridge.services.camera_service import capture_and_store, get_latest_snapshot_row
from bridge.services.persistence import apply_status_to_db
from bridge.services.response_builders import (
    ams_response,
    detail_response,
    list_item,
    panel_response,
)
from bridge.services.status_normalizer import normalize_mqtt_or_cloud_payload

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


@router.get("/printers", response_model=List[PrinterListItem])
def list_printers(session: Session = Depends(get_db)):
    printers = session.execute(select(Printer).where(Printer.is_active.is_(True))).scalars().all()
    out: List[PrinterListItem] = []
    for p in printers:
        c = _cache(session, p.id)
        out.append(list_item(p, c))
    return out


@router.get("/printers/{printer_id}", response_model=PrinterDetailResponse)
def printer_detail(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    return detail_response(p, _cache(session, p.id), _ams(session, p.id))


@router.get("/printers/{printer_id}/status", response_model=PrinterStatusPanel)
def printer_status(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    return panel_response(p, _cache(session, p.id))


@router.get("/printers/{printer_id}/ams")
def printer_ams(printer_id: int, session: Session = Depends(get_db)):
    p = _get_printer_or_404(session, printer_id)
    return ams_response(p.id, _ams(session, p.id)).model_dump()


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
