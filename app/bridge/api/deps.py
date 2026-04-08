from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from bridge.config import get_settings
from bridge.models.db import get_db

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


def verify_api_token(
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> None:
    settings = get_settings()
    expected = (settings.api_token or "").strip()
    if not expected:
        logger.warning("API_TOKEN não definido: endpoints protegidos estão abertos.")
        return
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Token Bearer obrigatório.")
    if creds.credentials != expected:
        raise HTTPException(status_code=403, detail="Token inválido.")


DbSession = Depends(get_db)
AuthDep = Depends(verify_api_token)
