"""Configuração do bambu-bridge a partir de variáveis de ambiente."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bambu_username: Optional[str] = Field(default=None, alias="BAMBU_USERNAME")
    bambu_password: Optional[str] = Field(default=None, alias="BAMBU_PASSWORD")
    bambu_token: Optional[str] = Field(default=None, alias="BAMBU_TOKEN")
    bambu_uid: Optional[str] = Field(default=None, alias="BAMBU_UID")
    bambu_region: str = Field(default="global", alias="BAMBU_REGION")
    bambu_token_file: Optional[str] = Field(default=None, alias="BAMBU_TOKEN_FILE")

    api_token: Optional[str] = Field(default=None, alias="API_TOKEN")

    database_url: str = Field(
        default="sqlite:///./storage/bambu_bridge.db",
        alias="DATABASE_URL",
    )

    snapshot_dir: str = Field(default="./storage/snapshots", alias="SNAPSHOT_DIR")
    snapshot_interval_seconds: int = Field(default=60, ge=5, alias="SNAPSHOT_INTERVAL_SECONDS")
    poll_interval_seconds: int = Field(default=120, ge=30, alias="POLL_INTERVAL_SECONDS")
    mqtt_stale_seconds: int = Field(default=90, ge=15, alias="MQTT_STALE_SECONDS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8010, alias="API_PORT")

    camera_timeout_seconds: float = Field(default=15.0, alias="CAMERA_TIMEOUT_SECONDS")
    printer_host_map_json: Optional[str] = Field(default=None, alias="BAMBU_PRINTER_HOST_MAP")

    history_max_per_printer: int = Field(default=100, ge=1, le=500, alias="HISTORY_MAX_PER_PRINTER")
    history_snapshot_interval_seconds: int = Field(
        default=60, ge=10, alias="HISTORY_SNAPSHOT_INTERVAL_SECONDS"
    )

    @field_validator("bambu_region")
    @classmethod
    def region_ok(cls, v: str) -> str:
        r = (v or "global").lower()
        if r not in ("global", "china"):
            return "global"
        return r

    def printer_host_map(self) -> Dict[str, str]:
        raw = self.printer_host_map_json
        if not raw or not raw.strip():
            return {}
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            pass
        return {}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_storage_dirs(settings: Optional[Settings] = None) -> None:
    s = settings or get_settings()
    db_path = s.database_url
    if db_path.startswith("sqlite:///"):
        path_part = db_path.replace("sqlite:///", "", 1)
        p = Path(path_part)
        if not p.is_absolute():
            p = Path.cwd() / p
        p.parent.mkdir(parents=True, exist_ok=True)
    snap = Path(s.snapshot_dir)
    if not snap.is_absolute():
        snap = Path.cwd() / snap
    snap.mkdir(parents=True, exist_ok=True)
