from __future__ import annotations

import json
import urllib.request
from typing import Any

from utils.service_contracts import CoreHealthContract, TraderStatusContract


def http_get_json(url: str, *, timeout_s: float = 5.0) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def fetch_core_health(base_url: str, *, timeout_s: float = 5.0) -> CoreHealthContract:
    payload = http_get_json(f"{str(base_url).rstrip('/')}/api/health", timeout_s=timeout_s)
    return CoreHealthContract.from_payload(payload)


def fetch_trader_status(base_url: str, *, timeout_s: float = 5.0) -> TraderStatusContract:
    payload = http_get_json(f"{str(base_url).rstrip('/')}/status", timeout_s=timeout_s)
    return TraderStatusContract.from_payload(payload)

