"""Montagem de schemas de resposta a partir das entidades."""

from __future__ import annotations

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


def list_item(p: Printer, cache: Optional[PrinterStatusCache]) -> PrinterListItem:
    return PrinterListItem(
        id=p.id,
        device_id=p.device_id,
        serial=p.serial,
        name=p.name or p.device_id,
        model=p.model,
        online=cache.online if cache else False,
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
                        name=s.get("name"),
                    )
                )
    if not slots:
        slots = [
            AmsSlot(slot=i, material=None, color=None, name=None) for i in range(1, 5)
        ]
    return AmsResponse(
        printer_id=printer_id,
        has_ams=ams.has_ams if ams else False,
        active_slot=ams.active_slot if ams else None,
        slots=slots,
        updated_at=ams.updated_at if ams else None,
    )


def detail_response(
    p: Printer,
    cache: Optional[PrinterStatusCache],
    ams: Optional[PrinterAmsCache],
) -> PrinterDetailResponse:
    ar = ams_response(p.id, ams)
    raw_hi = None
    if cache and cache.raw_payload_json and isinstance(cache.raw_payload_json, dict):
        print_d = cache.raw_payload_json.get("print")
        if isinstance(print_d, dict):
            raw_hi = {
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
        online=cache.online if cache else False,
        state=cache.state if cache else None,
        print_status=cache.print_status if cache else None,
        progress_percent=cache.progress_percent if cache else None,
        job_name=cache.job_name if cache else None,
        eta_minutes=cache.eta_minutes if cache else None,
        current_layer=cache.current_layer if cache else None,
        total_layers=cache.total_layers if cache else None,
        nozzle_temp=cache.nozzle_temp if cache else None,
        bed_temp=cache.bed_temp if cache else None,
        error_code=cache.error_code if cache else None,
        error_message=cache.error_message if cache else None,
        network_signal=cache.network_signal if cache else None,
        ams=ar,
        last_update=cache.updated_at if cache else None,
        raw_highlights=raw_hi,
    )


def panel_response(
    p: Printer,
    cache: Optional[PrinterStatusCache],
) -> PrinterStatusPanel:
    return PrinterStatusPanel(
        printer_id=p.id,
        name=p.name or p.device_id,
        online=cache.online if cache else False,
        state=cache.state if cache else None,
        print_status=cache.print_status if cache else None,
        progress_percent=cache.progress_percent if cache else None,
        job_name=cache.job_name if cache else None,
        eta_minutes=cache.eta_minutes if cache else None,
        current_layer=cache.current_layer if cache else None,
        total_layers=cache.total_layers if cache else None,
        nozzle_temp=cache.nozzle_temp if cache else None,
        bed_temp=cache.bed_temp if cache else None,
        last_seen=cache.updated_at if cache else None,
    )
