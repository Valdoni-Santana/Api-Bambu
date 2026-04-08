"""Ponto de entrada Uvicorn: bambu-bridge."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from sqlalchemy import select

from bridge.api.health import router as health_router
from bridge.api.printers import router as printers_router
from bridge.api.sync import router as sync_router
from bridge.config import ensure_storage_dirs, get_settings
from bridge.models.db import get_session_factory, init_db
from bridge.models.entities import Printer
from bridge.services.bambu_runtime import get_runtime
from bridge.services.camera_service import capture_and_store
from bridge.services.device_sync import list_active_device_ids, sync_devices_from_cloud
from bridge.services.polling import poll_cloud_status_once


def _setup_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def _snapshot_job() -> None:
    settings = get_settings()
    factory = get_session_factory()
    session = factory()
    try:
        printers = session.execute(select(Printer).where(Printer.is_active.is_(True))).scalars().all()
        for p in printers:
            try:
                capture_and_store(session, p.id)
                session.commit()
            except ValueError as e:
                session.rollback()
                logging.getLogger(__name__).debug(
                    "Snapshot não disponível para %s: %s", p.device_id, e
                )
            except Exception:
                session.rollback()
                logging.getLogger(__name__).warning(
                    "Falha no snapshot periódico: %s", p.device_id, exc_info=True
                )
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    log = logging.getLogger("bridge.main")
    settings = get_settings()
    ensure_storage_dirs(settings)
    init_db()

    rt = get_runtime()
    rt.configure(settings)

    if not rt.resolve_token_and_client():
        log.error("Serviço iniciando sem autenticação Bambu válida.")
    else:
        factory = get_session_factory()
        s = factory()
        ids: list = []
        try:
            try:
                n = sync_devices_from_cloud(s)
                s.commit()
                log.info("Sincronização inicial: %s impressora(s).", n)
            except Exception as e:
                s.rollback()
                log.warning("Sincronização inicial falhou: %s", e)
            ids = list_active_device_ids(s)
        finally:
            s.close()

        rt.start_mqtt_for_devices(ids)
        try:
            poll_cloud_status_once()
        except Exception as e:
            log.debug("Poll inicial: %s", e)

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(poll_cloud_status_once, "interval", seconds=settings.poll_interval_seconds, id="poll")
    sched.add_job(
        _snapshot_job,
        "interval",
        seconds=settings.snapshot_interval_seconds,
        id="snap",
    )
    sched.start()
    app.state.scheduler = sched

    yield

    sched = getattr(app.state, "scheduler", None)
    if sched:
        sched.shutdown(wait=False)
    rt.stop_mqtt()


app = FastAPI(
    title="bambu-bridge",
    description="API REST para integração NerdGeek com Bambu Lab (leitura/monitoramento).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(printers_router)
app.include_router(sync_router)


@app.get("/")
def root():
    return {
        "service": "bambu-bridge",
        "docs": "/docs",
        "health": "/health",
    }
