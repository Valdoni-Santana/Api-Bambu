"""Schemas Pydantic para respostas da API NerdGeek."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    api: str = "ok"
    db: str
    bambu_auth: str
    mqtt_connections: int
    printers_count: int


class PrinterListItem(BaseModel):
    id: int
    device_id: str
    serial: Optional[str] = None
    name: str
    model: Optional[str] = None
    online: bool
    last_update: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AmsSlot(BaseModel):
    slot: int
    material: Optional[str] = None
    color: Optional[str] = None
    name: Optional[str] = None


class AmsResponse(BaseModel):
    printer_id: int
    has_ams: bool
    active_slot: Optional[int] = None
    slots: List[AmsSlot] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class PrinterStatusPanel(BaseModel):
    printer_id: int
    name: str
    online: bool
    state: Optional[str] = None
    print_status: Optional[str] = None
    progress_percent: Optional[float] = None
    job_name: Optional[str] = None
    eta_minutes: Optional[int] = None
    current_layer: Optional[int] = None
    total_layers: Optional[int] = None
    nozzle_temp: Optional[float] = None
    bed_temp: Optional[float] = None
    last_seen: Optional[datetime] = None


class PrinterDetailResponse(BaseModel):
    printer_id: int
    device_id: str
    serial: Optional[str] = None
    name: str
    model: Optional[str] = None
    online: bool
    state: Optional[str] = None
    print_status: Optional[str] = None
    progress_percent: Optional[float] = None
    job_name: Optional[str] = None
    eta_minutes: Optional[int] = None
    current_layer: Optional[int] = None
    total_layers: Optional[int] = None
    nozzle_temp: Optional[float] = None
    bed_temp: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    network_signal: Optional[str] = None
    ams: Optional[AmsResponse] = None
    last_update: Optional[datetime] = None
    raw_highlights: Optional[dict] = None


class StatusHistoryItem(BaseModel):
    id: int
    state: Optional[str] = None
    print_status: Optional[str] = None
    progress_percent: Optional[float] = None
    job_name: Optional[str] = None
    nozzle_temp: Optional[float] = None
    bed_temp: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SyncResponse(BaseModel):
    synced: int
    message: str


class RefreshResponse(BaseModel):
    printer_id: int
    ok: bool
    message: str
