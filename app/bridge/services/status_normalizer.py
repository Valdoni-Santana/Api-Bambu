"""Normalização de payloads MQTT e cloud API para o modelo interno."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _extract_hms(print_data: dict, root: dict) -> Tuple[Optional[str], Optional[str]]:
    hms = print_data.get("hms") or root.get("hms")
    if hms is None:
        return None, None
    if isinstance(hms, list) and hms:
        first = hms[0] if isinstance(hms[0], dict) else {}
        code = str(first.get("code") or first.get("module_id") or first.get("attr") or "")
        msg = first.get("msg") or first.get("message") or json.dumps(first, default=str)[:500]
        return code or None, (str(msg) if msg else None)
    if isinstance(hms, dict):
        return str(hms.get("code", "")) or None, str(hms.get("message", "")) or None
    return None, str(hms)[:500]


def _job_name(print_data: dict) -> Optional[str]:
    for key in ("subtask_name", "gcode_file", "model_file", "file_name"):
        v = print_data.get(key)
        if v:
            return str(v)
    return None


def normalize_mqtt_or_cloud_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aceita mensagem MQTT completa ou dict de dispositivo da API print/bind.
    Retorna campos para status_cache + fragmento para AMS.
    """
    root = data
    print_data = data.get("print") if isinstance(data.get("print"), dict) else data

    online = bool(data.get("online", True))
    if "online" not in data and print_data is not data:
        online = True

    state = print_data.get("gcode_state") or data.get("gcode_state")
    print_status = data.get("print_status") or state

    mc_remaining = print_data.get("mc_remaining_time")
    eta_minutes = None
    if mc_remaining is not None:
        try:
            eta_minutes = max(0, int(float(mc_remaining)) // 60)
        except (TypeError, ValueError):
            eta_minutes = None

    err_code, err_msg = _extract_hms(print_data, root if isinstance(root, dict) else {})

    network_signal = data.get("wifi_signal")
    if network_signal is not None:
        network_signal = str(network_signal)

    ams_info = normalize_ams_from_payload(data)

    return {
        "online": online,
        "state": str(state) if state is not None else None,
        "print_status": str(print_status) if print_status is not None else None,
        "progress_percent": _as_float(print_data.get("mc_percent")),
        "job_name": _job_name(print_data),
        "eta_minutes": eta_minutes,
        "current_layer": _as_int(print_data.get("layer_num")),
        "total_layers": _as_int(print_data.get("total_layer_num")),
        "nozzle_temp": _as_float(print_data.get("nozzle_temper")),
        "bed_temp": _as_float(print_data.get("bed_temper")),
        "error_code": err_code,
        "error_message": err_msg,
        "network_signal": network_signal,
        "raw_payload_json": data if isinstance(data, dict) else {},
        "ams": ams_info,
    }


def normalize_ams_from_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Deriva has_ams, active_slot e slots[] a partir de MQTT ou API."""
    print_data = data.get("print") if isinstance(data.get("print"), dict) else data
    ams_root = data.get("ams") if isinstance(data.get("ams"), dict) else {}
    if not ams_root and isinstance(print_data, dict):
        ams_root = print_data.get("ams") if isinstance(print_data.get("ams"), dict) else {}

    slots_out: List[Dict[str, Any]] = []
    has_ams = False
    active_slot: Optional[int] = None

    # vitrine MQTT: ams.ams[] com tray[]
    inner = ams_root.get("ams")
    if isinstance(inner, list) and inner:
        has_ams = True
        unit = inner[0] if inner else {}
        trays = unit.get("tray") or unit.get("trays")
        if isinstance(trays, dict):
            trays = [trays]
        if isinstance(trays, list):
            for i, tray in enumerate(trays, start=1):
                if not isinstance(tray, dict):
                    continue
                slots_out.append(
                    {
                        "slot": _as_int(tray.get("id")) or i,
                        "material": tray.get("type") or tray.get("tray_type"),
                        "color": tray.get("color") or tray.get("tray_color_idx"),
                        "name": tray.get("tag_uid") or tray.get("cols") or None,
                    }
                )
        # slot ativo comum
        active_slot = _as_int(unit.get("tray_now")) or _as_int(unit.get("tray_tar"))

    # API get_ams_filaments / version
    if not slots_out and isinstance(data.get("ams_units"), list):
        has_ams = bool(data.get("has_ams"))
        for unit in data.get("ams_units") or []:
            for tray in unit.get("trays") or []:
                tid = tray.get("tray_id") or tray.get("id")
                slots_out.append(
                    {
                        "slot": _as_int(tid) or len(slots_out) + 1,
                        "material": tray.get("filament_type"),
                        "color": tray.get("filament_color"),
                        "name": None,
                    }
                )
        if slots_out:
            has_ams = True

    # normalizar para 4 slots painel
    by_slot: Dict[int, Dict[str, Any]] = {s["slot"]: s for s in slots_out if s.get("slot")}
    normalized_slots = []
    for n in range(1, 5):
        s = by_slot.get(n, {"slot": n, "material": None, "color": None, "name": None})
        normalized_slots.append(s)

    return {
        "has_ams": has_ams or len([x for x in slots_out if x.get("material")]) > 0,
        "active_slot": active_slot,
        "slots": normalized_slots,
        "raw_payload_json": ams_root if ams_root else None,
    }
