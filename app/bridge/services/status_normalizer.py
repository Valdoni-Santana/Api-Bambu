"""Normalização de payloads MQTT e cloud API para o modelo interno."""

from __future__ import annotations

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


def _first_present(data: Dict[str, Any], keys: List[str]) -> Tuple[Any, Optional[str]]:
    for k in keys:
        if k in data and data.get(k) is not None and data.get(k) != "":
            return data.get(k), k
    return None, None


def _pick_number(
    containers: List[Tuple[str, Dict[str, Any]]],
    keys: List[str],
    cast: str,
) -> Tuple[Optional[float | int], Optional[str]]:
    for prefix, src in containers:
        v, key = _first_present(src, keys)
        if key is None:
            continue
        if cast == "float":
            parsed = _as_float(v)
        else:
            parsed = _as_int(v)
        if parsed is not None:
            return parsed, f"{prefix}.{key}"
    return None, None


def _pick_str(
    containers: List[Tuple[str, Dict[str, Any]]],
    keys: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    for prefix, src in containers:
        v, key = _first_present(src, keys)
        if key is None:
            continue
        return str(v), f"{prefix}.{key}"
    return None, None


def _first_non_null_slot(slots: List[Dict[str, Any]]) -> bool:
    for s in slots:
        if not isinstance(s, dict):
            continue
        if s.get("material") or s.get("color") or s.get("name") or s.get("type"):
            return True
    return False


def _normalize_color(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    if raw is None:
        return None, None
    s = str(raw).strip().lstrip("#").upper()
    if not s:
        return None, None
    if len(s) == 8:
        # Bambu costuma usar RRGGBBAA.
        return s, f"#{s[:6]}"
    if len(s) == 6:
        return s, f"#{s}"
    return s, None


def _physical_slot_from_source_index(source_index: Optional[int], fallback_slot: Optional[int], *, zero_based: bool) -> Optional[int]:
    if source_index is not None:
        return source_index + 1 if zero_based else source_index
    return fallback_slot


def _detect_external_filament(containers: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, Any]:
    mat, _ = _pick_str(
        containers,
        ["tray_type", "filament_type", "spool_type", "material", "external_filament_type"],
    )
    color, _ = _pick_str(
        containers,
        ["tray_color", "filament_color", "spool_color", "color", "external_filament_color"],
    )
    name, _ = _pick_str(
        containers,
        ["tray_name", "filament_name", "spool_name", "name", "external_filament_name"],
    )
    ftype, _ = _pick_str(
        containers,
        ["type", "tray_type", "spool_type", "external_type"],
    )
    source = "external" if (mat or color or name or ftype) else "unknown"
    return {
        "external_spool_configured": bool(mat or color or name or ftype),
        "filament_source": source,
        "filament": {
            "source": source,
            "material": mat,
            "color": color,
            "name": name,
            "type": ftype,
        },
    }


def _extract_hms(print_data: dict, root: dict) -> Tuple[Optional[str], Optional[str]]:
    hms = print_data.get("hms") or root.get("hms") or print_data.get("errors") or root.get("errors")
    if hms is None:
        return None, None
    if isinstance(hms, list) and hms:
        first = hms[0] if isinstance(hms[0], dict) else {}
        code = str(first.get("code") or first.get("module_id") or first.get("attr") or "")
        msg = first.get("msg") or first.get("message") or str(first)[:500]
        return code or None, (str(msg) if msg else None)
    if isinstance(hms, dict):
        return str(hms.get("code", "")) or None, str(hms.get("message", "")) or None
    return None, str(hms)[:500]


def _job_name(containers: List[Tuple[str, Dict[str, Any]]]) -> Tuple[Optional[str], Optional[str]]:
    keys = [
        "subtask_name",
        "gcode_file",
        "job_name",
        "filename",
        "file_name",
        "task_name",
        "project_name",
        "model_file",
    ]
    return _pick_str(containers, keys)


def _nested_payloads(root: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = [("payload", root)]
    for key in ("print", "status", "report", "data"):
        obj = root.get(key)
        if isinstance(obj, dict):
            out.append((f"payload.{key}", obj))
    status_obj = root.get("status")
    if isinstance(status_obj, dict):
        for key in ("print", "job", "progress", "temp", "temps"):
            obj = status_obj.get(key)
            if isinstance(obj, dict):
                out.append((f"payload.status.{key}", obj))
    return out


def normalize_mqtt_or_cloud_payload_with_debug(data: Dict[str, Any]) -> Dict[str, Any]:
    root = data if isinstance(data, dict) else {}
    containers = _nested_payloads(root)
    by_name = {k: v for k, v in containers}
    print_data = by_name.get("payload.print", root)

    online_raw, online_src = _pick_str(containers, ["online", "is_online", "device_online"])
    if online_raw is None:
        online = True if print_data is not root else False
    else:
        online = str(online_raw).lower() in ("1", "true", "yes", "online")

    state, state_src = _pick_str(
        containers,
        ["gcode_state", "state", "stg", "print_state", "mc_print_stage", "print_stage"],
    )
    print_status, print_status_src = _pick_str(
        containers,
        ["print_status", "status", "gcode_state", "mc_print_state", "job_status"],
    )
    if print_status is None:
        print_status = state
        print_status_src = state_src

    progress_percent, progress_src = _pick_number(
        containers,
        ["mc_percent", "progress", "print_percent", "percent", "progress_percent"],
        "float",
    )
    eta_minutes, eta_src = _pick_number(
        containers,
        ["mc_remaining_time_min", "remaining_minutes", "eta_minutes"],
        "int",
    )
    if eta_minutes is None:
        eta_seconds, eta_seconds_src = _pick_number(
            containers,
            ["mc_remaining_time", "remaining_time", "eta_sec", "remaining_seconds"],
            "int",
        )
        if eta_seconds is not None:
            eta_minutes = max(0, int(eta_seconds) // 60)
            eta_src = f"{eta_seconds_src}->minutes" if eta_seconds_src else None

    current_layer, current_layer_src = _pick_number(
        containers, ["layer_num", "current_layer", "layer", "layer_now"], "int"
    )
    total_layers, total_layers_src = _pick_number(
        containers, ["total_layer_num", "total_layers", "layer_total"], "int"
    )
    nozzle_temp, nozzle_src = _pick_number(
        containers,
        ["nozzle_temper", "nozzle_temp", "hotend_temp", "temp_nozzle"],
        "float",
    )
    bed_temp, bed_src = _pick_number(
        containers, ["bed_temper", "bed_temp", "temp_bed", "heatbed_temp"], "float"
    )

    job_name, job_src = _job_name(containers)
    gcode_file, gcode_src = _pick_str(containers, ["gcode_file", "file_name", "filename"])
    subtask_name, subtask_src = _pick_str(containers, ["subtask_name", "task_name"])
    task_id, task_id_src = _pick_str(containers, ["task_id", "project_id", "job_id"])

    err_code, err_msg = _extract_hms(print_data, root)
    if err_code is None:
        err_code, _ = _pick_str(containers, ["error_code", "err_code", "hms_code", "code"])
    if err_msg is None:
        err_msg, _ = _pick_str(
            containers, ["error_message", "err_msg", "message", "hms_message", "error"]
        )

    network_signal, network_src = _pick_str(
        containers, ["wifi_signal", "network_signal", "rssi", "signal"]
    )
    ams_info = normalize_ams_from_payload(data)

    highlights = {
        "gcode_file": gcode_file,
        "subtask_name": subtask_name,
        "task_id": task_id,
    }
    field_sources = {
        "state": state_src,
        "print_status": print_status_src,
        "progress_percent": progress_src,
        "job_name": job_src,
        "eta_minutes": eta_src,
        "current_layer": current_layer_src,
        "total_layers": total_layers_src,
        "nozzle_temp": nozzle_src,
        "bed_temp": bed_src,
        "error_code": "hms/payload",
        "error_message": "hms/payload",
        "network_signal": network_src,
        "gcode_file": gcode_src,
        "subtask_name": subtask_src,
        "task_id": task_id_src,
        "online": online_src,
    }

    return {
        "online": online,
        "state": state,
        "print_status": print_status,
        "progress_percent": progress_percent,
        "job_name": job_name,
        "eta_minutes": eta_minutes,
        "current_layer": current_layer,
        "total_layers": total_layers,
        "nozzle_temp": nozzle_temp,
        "bed_temp": bed_temp,
        "error_code": err_code,
        "error_message": err_msg,
        "network_signal": network_signal,
        "raw_payload_json": root,
        "ams": ams_info,
        "raw_highlights": highlights,
        "field_sources": field_sources,
    }


def normalize_mqtt_or_cloud_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aceita mensagem MQTT completa ou dict de dispositivo da API print/bind.
    Retorna campos para status_cache + fragmento para AMS.
    """
    out = normalize_mqtt_or_cloud_payload_with_debug(data)
    out.pop("field_sources", None)
    return out


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
                        "material": tray.get("type") or tray.get("tray_type") or tray.get("tray_sub_brands"),
                        "color": tray.get("color")
                        or tray.get("tray_color")
                        or tray.get("tray_color_idx")
                        or tray.get("tray_col"),
                        "color_raw": (tray.get("tray_color") or tray.get("color") or (tray.get("cols") or [None])[0]),
                        "color_hex": None,
                        "name": tray.get("tray_name")
                        or tray.get("tray_info_idx")
                        or tray.get("tag_uid")
                        or tray.get("cols")
                        or None,
                        "type": tray.get("tray_type") or tray.get("type"),
                        "source_index": _as_int(tray.get("id")),
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
                        "color_raw": tray.get("filament_color"),
                        "color_hex": None,
                        "name": tray.get("filament_name"),
                        "type": tray.get("tray_type"),
                        "source_index": _as_int(tid),
                    }
                )
        if slots_out:
            has_ams = True

    # fallback por listas comuns em payloads A1
    if not slots_out:
        tray_list = ams_root.get("tray") or ams_root.get("trays")
        if isinstance(tray_list, dict):
            tray_list = [tray_list]
        if isinstance(tray_list, list):
            # Apenas estrutura não significa AMS físico confirmado.
            for i, tray in enumerate(tray_list, start=1):
                if not isinstance(tray, dict):
                    continue
                slots_out.append(
                    {
                        "slot": _as_int(tray.get("id")) or i,
                        "material": tray.get("tray_type") or tray.get("type"),
                        "color": tray.get("tray_color") or tray.get("color"),
                        "color_raw": tray.get("tray_color") or tray.get("color"),
                        "color_hex": None,
                        "name": tray.get("tray_name") or tray.get("tray_info_idx"),
                        "type": tray.get("type") or tray.get("tray_type"),
                        "source_index": _as_int(tray.get("id")),
                    }
                )

    # Regra global de indexação:
    # - Se qualquer source_index==0, tratamos índices de origem como 0-based e mapeamos para slot físico +1.
    # - Caso contrário, preservamos o índice informado.
    has_zero_based_index = any(
        isinstance(s, dict) and isinstance(s.get("source_index"), int) and s.get("source_index") == 0
        for s in slots_out
    )
    remapped_slots: List[Dict[str, Any]] = []
    for s in slots_out:
        if not isinstance(s, dict):
            continue
        src_idx = s.get("source_index")
        if not isinstance(src_idx, int):
            src_idx = _as_int(src_idx)
        mapped = dict(s)
        mapped_slot = _physical_slot_from_source_index(
            src_idx,
            _as_int(s.get("slot")),
            zero_based=has_zero_based_index,
        )
        if mapped_slot is not None:
            mapped["slot"] = mapped_slot
        if src_idx is not None:
            mapped["source_index"] = src_idx
        remapped_slots.append(mapped)

    # normalizar para 4 slots painel
    by_slot: Dict[int, Dict[str, Any]] = {s["slot"]: s for s in remapped_slots if s.get("slot")}
    normalized_slots = []
    for n in range(1, 5):
        s = by_slot.get(
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
        c_raw, c_hex = _normalize_color(s.get("color_raw") or s.get("color"))
        s["color_raw"] = c_raw
        s["color_hex"] = c_hex
        # compat: mantém color como valor bruto normalizado (sem #).
        s["color"] = c_raw
        normalized_slots.append(s)

    has_real_slot_data = _first_non_null_slot(normalized_slots)
    has_ams = bool(has_real_slot_data or active_slot is not None or ams_root)
    ams_detected_struct = bool(ams_root or inner or data.get("ams_units") or data.get("has_ams"))
    containers = _nested_payloads(data if isinstance(data, dict) else {})
    external = _detect_external_filament(containers)
    # Só afirmar fonte AMS quando há evidência de dados de filamento/slot ativo.
    filament_source = "ams" if (has_real_slot_data or active_slot is not None) else external["filament_source"]

    return {
        "has_ams": has_ams,
        "ams_detected_struct": ams_detected_struct,
        "external_spool_configured": external["external_spool_configured"],
        "filament_source": filament_source,
        "filament": external["filament"],
        "active_slot": active_slot,
        "slots": normalized_slots,
        "raw_payload_json": {
            "ams_root": ams_root if ams_root else None,
            "slots": normalized_slots,
            "slots_raw_found": remapped_slots,
            "ams_detected_struct": ams_detected_struct,
            "external_spool_configured": external["external_spool_configured"],
            "filament_source": filament_source,
            "filament": external["filament"],
        },
    }
