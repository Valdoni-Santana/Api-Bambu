#!/usr/bin/env python3
"""
Smoke test HTTP do bambu-bridge.
Uso:
  set PYTHONPATH=app   (Windows PowerShell: $env:PYTHONPATH="app")
  python scripts/smoke_test.py --base http://127.0.0.1:8010 --token SEU_API_TOKEN
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

try:
    import requests
except ImportError:
    print("Instale requests: pip install requests", file=sys.stderr)
    sys.exit(1)


def get_json(url: str, headers: Dict[str, str]) -> tuple[int, Any]:
    r = requests.get(url, headers=headers, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke test bambu-bridge")
    p.add_argument("--base", default="http://127.0.0.1:8010", help="URL base do serviço")
    p.add_argument("--token", default=None, help="API_TOKEN (Bearer); opcional se API_TOKEN vazio no servidor")
    args = p.parse_args()
    base = args.base.rstrip("/")
    headers: Dict[str, str] = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    print("GET /health")
    code, data = get_json(f"{base}/health", {})
    print(code, json.dumps(data, indent=2) if isinstance(data, dict) else data)
    if code != 200:
        return 1

    print("\nGET /api/v1/printers")
    code, data = get_json(f"{base}/api/v1/printers", headers)
    print(code, json.dumps(data, indent=2) if isinstance(data, (list, dict)) else data)
    if code != 200:
        return 1
    if not isinstance(data, list) or not data:
        print("Nenhuma impressora — demais testes pulados (configure conta Bambu).")
        return 0

    pid = data[0]["id"]
    for path in (
        f"/api/v1/printers/{pid}",
        f"/api/v1/printers/{pid}/status",
        f"/api/v1/printers/{pid}/ams",
        f"/api/v1/printers/{pid}/history?limit=10",
    ):
        print(f"\nGET {path}")
        code, body = get_json(f"{base}{path}", headers)
        print(code, json.dumps(body, indent=2) if isinstance(body, (list, dict)) else body)
        if code != 200:
            return 1

    print(f"\nGET /api/v1/printers/{pid}/camera/snapshot (sem refresh)")
    r = requests.get(f"{base}/api/v1/printers/{pid}/camera/snapshot", headers=headers, timeout=60)
    print(r.status_code, r.headers.get("content-type"), len(r.content), "bytes")
    if r.status_code not in (200, 404, 503):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
