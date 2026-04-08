"""Coleta de snapshot: URL cloud (quando existir) ou stream JPEG local (A1/P1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from bambulab.client import BambuAPIError
from bambulab.video import JPEGFrameStream, VideoStreamError

from bridge.config import get_settings
from bridge.models.entities import Printer, PrinterSnapshot
from bridge.services.bambu_runtime import get_runtime

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _find_snapshot_url(info: dict) -> Optional[str]:
    for key, val in info.items():
        if not isinstance(val, str):
            continue
        lk = key.lower()
        if any(x in lk for x in ("snapshot", "image", "jpeg", "jpg", "url")):
            if val.startswith("http://") or val.startswith("https://"):
                return val
    return info.get("url") if isinstance(info.get("url"), str) else None


def _collect_candidate_urls(info: dict) -> List[str]:
    urls: List[str] = []
    for key, val in info.items():
        if isinstance(val, str):
            lk = key.lower()
            if any(x in lk for x in ("snapshot", "image", "jpeg", "jpg", "url")) and val.startswith(
                ("http://", "https://")
            ):
                urls.append(val)
        elif isinstance(val, dict):
            for _, inner in val.items():
                if isinstance(inner, str) and inner.startswith(("http://", "https://")):
                    urls.append(inner)
    dedup = []
    seen = set()
    for u in urls:
        if u not in seen:
            dedup.append(u)
            seen.add(u)
    return dedup


def fetch_snapshot_bytes_for_printer(session: Session, printer: Printer) -> Tuple[bytes, str]:
    """
    Retorna (jpeg_bytes, fonte_descricao).
    Raises ValueError com mensagem amigável se indisponível.
    """
    rt = get_runtime()
    settings = get_settings()
    timeout = settings.camera_timeout_seconds

    if not rt.client:
        raise ValueError("Cliente cloud não disponível.")

    reasons = []
    # 1) Tentar URL da API (snapshot / camera_urls)
    try:
        urls = rt.client.get_camera_urls(printer.device_id)
        candidates = _collect_candidate_urls(urls)
        snap_url = _find_snapshot_url(urls)
        if snap_url and snap_url not in candidates:
            candidates.insert(0, snap_url)
        for c in candidates:
            try:
                r = requests.get(c, timeout=timeout, headers={"User-Agent": "bambu-bridge/1.0"})
                r.raise_for_status()
                data = r.content
                if data and len(data) > 100:
                    logger.info("Snapshot cloud obtido para %s via %s", printer.device_id, c)
                    return data, "cloud_url"
                reasons.append(f"url sem imagem válida: {c}")
            except requests.RequestException as e:
                reasons.append(f"falha em {c}: {e}")
    except (BambuAPIError, requests.RequestException) as e:
        reasons.append(f"get_camera_urls falhou: {e}")
        logger.debug("Snapshot URL cloud indisponível para %s: %s", printer.device_id, e)

    # 1b) Tentar endpoint de credenciais como fallback (algumas contas trazem URLs adicionais)
    try:
        creds = rt.client.get_camera_credentials(printer.device_id)
        alt_candidates = _collect_candidate_urls(creds)
        for c in alt_candidates:
            try:
                r = requests.get(c, timeout=timeout, headers={"User-Agent": "bambu-bridge/1.0"})
                r.raise_for_status()
                data = r.content
                if data and len(data) > 100:
                    logger.info("Snapshot cloud alternativo obtido para %s via %s", printer.device_id, c)
                    return data, "cloud_alt"
            except requests.RequestException as e:
                reasons.append(f"fallback credencial falhou {c}: {e}")
    except Exception as e:
        reasons.append(f"get_camera_credentials sem URL útil: {e}")

    # 2) Stream JPEG local (LAN)
    host_map = settings.printer_host_map()
    host = host_map.get(printer.device_id)
    code = printer.access_code
    if not host or not code:
        msg = (
            "Câmera não disponível remotamente neste ambiente. "
            "Configure BAMBU_PRINTER_HOST_MAP com o IP da impressora na LAN "
            "(JSON: {\"<device_id>\": \"192.168.x.x\"}) para snapshots A1/P1 via JPEG."
        )
        if reasons:
            msg += f" Motivos cloud: {' | '.join(reasons[:4])}"
        raise ValueError(msg)

    stream = JPEGFrameStream(host, code)
    try:
        stream.connect()
        frame = stream.get_frame()
        stream.disconnect()
        if not frame or len(frame) < 100:
            raise ValueError("Frame vazio ou inválido.")
        return frame, "local_jpeg"
    except VideoStreamError as e:
        logger.warning("Stream de câmera local falhou (%s): %s", printer.device_id, e)
        extra = f" Motivos cloud: {' | '.join(reasons[:4])}" if reasons else ""
        raise ValueError(f"Timeout ou falha ao conectar na câmera local: {e}.{extra}") from e
    finally:
        try:
            stream.disconnect()
        except Exception:
            pass


def save_snapshot(session: Session, printer: Printer, data: bytes, source: str) -> PrinterSnapshot:
    settings = get_settings()
    snap_dir = Path(settings.snapshot_dir)
    if not snap_dir.is_absolute():
        snap_dir = Path.cwd() / snap_dir
    snap_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{printer.device_id}_{int(utcnow().timestamp())}.jpg"
    path = snap_dir / fname
    path.write_bytes(data)
    row = PrinterSnapshot(
        printer_id=printer.id,
        file_path=str(path.resolve()),
        mime_type="image/jpeg",
        size_bytes=len(data),
        captured_at=utcnow(),
    )
    session.add(row)
    session.flush()
    logger.info("Snapshot salvo printer_id=%s bytes=%s source=%s", printer.id, len(data), source)
    return row


def capture_and_store(session: Session, printer_id: int) -> PrinterSnapshot:
    printer = session.get(Printer, printer_id)
    if not printer:
        raise ValueError("Impressora não encontrada.")
    data, source = fetch_snapshot_bytes_for_printer(session, printer)
    return save_snapshot(session, printer, data, source)


def get_latest_snapshot_row(session: Session, printer_id: int) -> Optional[PrinterSnapshot]:
    return session.execute(
        select(PrinterSnapshot)
        .where(PrinterSnapshot.printer_id == printer_id)
        .order_by(PrinterSnapshot.captured_at.desc())
        .limit(1)
    ).scalar_one_or_none()
