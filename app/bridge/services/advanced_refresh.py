"""Refresh avançado somente leitura para tentar payload mais completo.

Segurança:
- Usa apenas operações read-only:
  - MQTT pushall (request_full_status) para solicitar status completo
  - Cloud get_print_status(force=True)
  - Cloud get_ams_filaments(device_id)
- NÃO usa qualquer comando de controle de impressão/eixos/temperatura.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from bridge.config import get_settings
from bridge.models.entities import Printer, PrinterStatusCache
from bridge.services.bambu_runtime import get_runtime
from bridge.services.persistence import apply_status_to_db
from bridge.services.status_normalizer import normalize_mqtt_or_cloud_payload_with_debug

KEY_FIELDS = [
    "state",
    "print_status",
    "progress_percent",
    "job_name",
    "eta_minutes",
    "current_layer",
    "total_layers",
]


def _nullish(v: Any) -> bool:
    return v is None or v == ""


def should_advanced_refresh(cache: Optional[PrinterStatusCache]) -> bool:
    if cache is None:
        return True
    return any(_nullish(getattr(cache, k, None)) for k in KEY_FIELDS)


def _completeness(norm: Dict[str, Any]) -> int:
    return sum(1 for k in KEY_FIELDS if not _nullish(norm.get(k)))


def _tag_sources(field_sources: Dict[str, Any], label: str) -> Dict[str, Any]:
    out = {}
    for k, v in (field_sources or {}).items():
        out[k] = f"{label}:{v}" if v else label
    return out


def run_advanced_refresh(
    session: Session,
    printer: Printer,
    *,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    timeout = timeout_seconds or settings.advanced_refresh_timeout_seconds
    rt = get_runtime()
    start = time.perf_counter()

    if not settings.advanced_refresh_enabled:
        return {
            "success": False,
            "method_used": "disabled",
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "obtained_fields": {},
            "field_sources": {},
        }

    best_norm: Optional[Dict[str, Any]] = None
    best_method = "none"
    best_score = -1

    # Método 1: MQTT pushall (somente leitura)
    pushed = rt.request_pushall(printer.device_id)
    if pushed:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            raw = rt.get_last_mqtt(printer.device_id)
            if isinstance(raw, dict):
                cand = normalize_mqtt_or_cloud_payload_with_debug(raw)
                score = _completeness(cand)
                if score > best_score:
                    best_norm = cand
                    best_score = score
                    best_method = "mqtt_pushall"
                if score >= 5:
                    break
            time.sleep(0.25)

    # Método 2: cloud poll forçado (somente leitura)
    if rt.client:
        try:
            resp = rt.client.get_print_status(force=True)
            for d in resp.get("devices") or []:
                if d.get("dev_id") == printer.device_id:
                    cand = normalize_mqtt_or_cloud_payload_with_debug(d)
                    score = _completeness(cand)
                    if score > best_score:
                        best_norm = cand
                        best_score = score
                        best_method = "cloud_force_poll"
                    break
        except Exception:
            pass

        # Método 3: AMS dedicado (somente leitura)
        try:
            ams = rt.client.get_ams_filaments(printer.device_id)
            if best_norm is None:
                best_norm = normalize_mqtt_or_cloud_payload_with_debug({})
                best_method = "cloud_ams_only"
            best_norm["ams"] = best_norm.get("ams") or {}
            best_norm["ams"].update(
                normalize_mqtt_or_cloud_payload_with_debug(ams).get("ams") or {}
            )
        except Exception:
            pass

    if best_norm is None:
        return {
            "success": False,
            "method_used": "none",
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "obtained_fields": {},
            "field_sources": {},
        }

    # Marcação da origem do refresh para debug.
    raw_payload = best_norm.get("raw_payload_json")
    if isinstance(raw_payload, dict):
        raw_payload["_meta"] = {
            "source_label": f"advanced refresh/{best_method}",
        }
    best_norm["field_sources"] = _tag_sources(
        best_norm.get("field_sources") or {}, f"advanced refresh/{best_method}"
    )
    apply_status_to_db(session, printer.id, best_norm, append_history=True)

    obtained = {k: best_norm.get(k) for k in KEY_FIELDS}
    return {
        "success": best_score > 0,
        "method_used": best_method,
        "duration_ms": int((time.perf_counter() - start) * 1000),
        "obtained_fields": obtained,
        "field_sources": best_norm.get("field_sources") or {},
    }

