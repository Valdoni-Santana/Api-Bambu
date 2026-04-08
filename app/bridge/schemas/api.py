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
    color_raw: Optional[str] = None
    color_hex: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    source_index: Optional[int] = None


class AmsResponse(BaseModel):
    printer_id: int
    has_ams: bool
    active_slot: Optional[int] = None
    slots: List[AmsSlot] = Field(default_factory=list)
    updated_at: Optional[datetime] = None
    cache_preserved: bool = False
    quality_score: int = 0
    ams_status: str = "pending_refresh"
    last_good_update_at: Optional[datetime] = None
    context_job_name: Optional[str] = None
    context_task_id: Optional[str] = None
    data_source: Optional[str] = None
    ams_detected_struct: bool = False
    external_spool_configured: bool = False
    filament_source: str = "unknown"
    filament: dict = Field(default_factory=dict)
    ams_capability_confirmed: bool = False
    ams_capability_confidence: str = "low"
    ams_last_confirmed_at: Optional[datetime] = None
    ams_last_confirmed_source: Optional[str] = None


class PrinterStatusPanel(BaseModel):
    printer_id: int
    name: str
    online: bool
    state: Optional[str] = None
    derived_state: Optional[str] = None
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
    error_attr: Optional[int] = None
    error_action: Optional[int] = None
    error_timestamp: Optional[int] = None
    error_raw: Optional[dict] = None
    last_seen: Optional[datetime] = None


class PrinterDetailResponse(BaseModel):
    printer_id: int
    device_id: str
    serial: Optional[str] = None
    name: str
    model: Optional[str] = None
    online: bool
    state: Optional[str] = None
    derived_state: Optional[str] = None
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
    error_attr: Optional[int] = None
    error_action: Optional[int] = None
    error_timestamp: Optional[int] = None
    error_raw: Optional[dict] = None
    network_signal: Optional[str] = None
    ams: Optional[AmsResponse] = None
    cache_preserved: Optional[bool] = None
    quality_score: Optional[int] = None
    ams_status: Optional[str] = None
    last_good_update_at: Optional[datetime] = None
    context_job_name: Optional[str] = None
    context_task_id: Optional[str] = None
    data_source: Optional[str] = None
    ams_detected_struct: Optional[bool] = None
    external_spool_configured: Optional[bool] = None
    filament_source: Optional[str] = None
    filament: Optional[dict] = None
    ams_capability_confirmed: Optional[bool] = None
    ams_capability_confidence: Optional[str] = None
    ams_last_confirmed_at: Optional[datetime] = None
    ams_last_confirmed_source: Optional[str] = None
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


class AdvancedRefreshResponse(BaseModel):
    printer_id: int
    success: bool
    method_used: str
    duration_ms: int
    obtained_fields: dict
    field_sources: dict
