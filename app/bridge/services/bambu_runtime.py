"""Estado compartilhado: cliente Bambu, MQTT, token e locks."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from bambulab import BambuClient, MQTTClient
from bambulab.auth import BambuAuthError, BambuAuthenticator
from bambulab.client import BambuAPIError

from bridge.config import Settings, get_settings
from bridge.models.db import get_session_factory
from bridge.models.entities import Printer
from bridge.services.persistence import apply_status_to_db
from bridge.services.status_normalizer import normalize_mqtt_or_cloud_payload

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BridgeRuntime:
    """
    Mantém token, cliente HTTP, clientes MQTT por device_id e métricas de health.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.settings: Optional[Settings] = None
        self.token: Optional[str] = None
        self.uid: Optional[str] = None
        self.client: Optional[BambuClient] = None
        self.auth_ok: bool = False
        self.auth_error: Optional[str] = None
        self.mqtt_clients: Dict[str, MQTTClient] = {}
        self._last_mqtt_payload: Dict[str, Dict[str, Any]] = {}
        self._last_history_at: Dict[int, datetime] = {}
        self._mqtt_failures: Dict[str, int] = {}

    def configure(self, settings: Settings) -> None:
        with self._lock:
            self.settings = settings

    def resolve_token_and_client(self) -> bool:
        with self._lock:
            self.settings = self.settings or get_settings()
            s = self.settings
            self.auth_error = None
            self.auth_ok = False
            self.client = None
            self.token = None

            token = (s.bambu_token or "").strip() or None
            token_file = s.bambu_token_file

            if not token and token_file:
                try:
                    auth_tf = BambuAuthenticator(region=s.bambu_region, token_file=token_file)
                    token = auth_tf.load_token()
                except Exception as e:
                    logger.warning("Não foi possível ler token do arquivo configurado: %s", e)

            if not token:
                try:
                    auth = BambuAuthenticator(region=s.bambu_region)
                    token = auth.load_token()
                except Exception:
                    pass

            if not token and s.bambu_username and s.bambu_password:
                try:
                    auth = BambuAuthenticator(region=s.bambu_region)
                    token = auth.login(s.bambu_username, s.bambu_password)
                    logger.info("Login Bambu concluído (token obtido).")
                except BambuAuthError as e:
                    self.auth_error = str(e)
                    logger.error("Falha de autenticação Bambu: %s", e)
                    return False

            if not token:
                self.auth_error = (
                    "Token ausente. Defina BAMBU_TOKEN, use BAMBU_TOKEN_FILE / ~/.bambu_token "
                    "ou BAMBU_USERNAME + BAMBU_PASSWORD (2FA requer CLI)."
                )
                logger.error(self.auth_error)
                return False

            self.token = token
            self.client = BambuClient(token)
            try:
                if s.bambu_uid:
                    self.uid = str(s.bambu_uid).strip()
                else:
                    prof = self.client.get_user_profile()
                    self.uid = str(prof.get("uid") or prof.get("user_id") or "")
                if not self.uid:
                    self.auth_error = "UID não encontrado no perfil; defina BAMBU_UID."
                    logger.error(self.auth_error)
                    return False
            except BambuAPIError as e:
                self.auth_error = f"Token inválido ou expirado: {e}"
                logger.error(self.auth_error)
                return False

            self.auth_ok = True
            return True

    def mqtt_connected_count(self) -> int:
        with self._lock:
            n = 0
            for c in self.mqtt_clients.values():
                if c.connected:
                    n += 1
            return n

    def stop_mqtt(self) -> None:
        with self._lock:
            for dev_id, cli in list(self.mqtt_clients.items()):
                try:
                    cli.disconnect()
                except Exception as e:
                    logger.debug("MQTT disconnect %s: %s", dev_id, e)
            self.mqtt_clients.clear()

    def start_mqtt_for_devices(self, device_ids: List[str]) -> None:
        with self._lock:
            if not self.client or not self.token or not self.uid:
                logger.warning("MQTT não iniciado: cliente/UID ausente.")
                return

            for dev_id in device_ids:
                if dev_id in self.mqtt_clients:
                    continue

                def make_cb(did: str):
                    def _cb(_device_id: str, data: Dict[str, Any]):
                        self._on_mqtt_message(did, data)

                    return _cb

                try:
                    cli = MQTTClient(
                        username=self.uid,
                        access_token=self.token,
                        device_id=dev_id,
                        on_message=make_cb(dev_id),
                    )
                    cli.connect(blocking=False)
                    self.mqtt_clients[dev_id] = cli
                    logger.info("MQTT iniciado para device_id=%s", dev_id)
                except Exception as e:
                    self._mqtt_failures[dev_id] = self._mqtt_failures.get(dev_id, 0) + 1
                    logger.warning("Falha MQTT para %s: %s", dev_id, e)

    def _on_mqtt_message(self, device_id: str, data: Dict[str, Any]) -> None:
        self._last_mqtt_payload[device_id] = data
        factory = get_session_factory()
        session = factory()
        try:
            row = session.execute(
                select(Printer).where(Printer.device_id == device_id)
            ).scalar_one_or_none()
            if row is None:
                return
            norm = normalize_mqtt_or_cloud_payload(data)
            hist = self._should_append_history(row.id)
            apply_status_to_db(session, row.id, norm, append_history=hist)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.exception("Erro ao persistir MQTT para %s: %s", device_id, e)
        finally:
            session.close()

    def _should_append_history(self, printer_id: int) -> bool:
        s = self.settings or get_settings()
        interval = s.history_snapshot_interval_seconds
        now = utcnow()
        last = self._last_history_at.get(printer_id)
        if last is None or (now - last).total_seconds() >= interval:
            self._last_history_at[printer_id] = now
            return True
        return False

    def get_last_mqtt(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._last_mqtt_payload.get(device_id)

    def request_pushall(self, device_id: str) -> bool:
        with self._lock:
            cli = self.mqtt_clients.get(device_id)
        if not cli or not cli.connected:
            return False
        try:
            cli.request_full_status()
            return True
        except Exception as e:
            logger.warning("pushall falhou para %s: %s", device_id, e)
            return False


_RUNTIME = BridgeRuntime()


def get_runtime() -> BridgeRuntime:
    return _RUNTIME
