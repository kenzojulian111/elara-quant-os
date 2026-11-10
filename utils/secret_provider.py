import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    from pydantic import validate_call
except ImportError:
    def validate_call(*args, **kwargs):
        if len(args) == 1 and callable(args[0]): return args[0]
        def decorator(func): return func
        return decorator


def _normalize_name(name: str) -> str:
    return str(name or "").strip()


def _parse_json_mapping(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        payload = json.loads(str(raw))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=256)
def _file_secret_map(path: str, mtime_ns: int) -> dict:
    _ = mtime_ns
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _parse_json_mapping(f.read())
    except Exception:
        return {}


def _get_from_file(name: str) -> Optional[str]:
    file_path = str(os.getenv("SECRET_MANAGER_FILE", "") or "").strip()
    if not file_path:
        return None
    try:
        mtime_ns = int(Path(file_path).stat().st_mtime_ns)
    except Exception:
        return None
    payload = _file_secret_map(file_path, mtime_ns)
    value = payload.get(name)
    if value is None:
        return None
    return str(value)


def _get_from_aws(name: str) -> Optional[str]:
    secret_id = str(os.getenv("AWS_SECRET_ID", "") or "").strip()
    region = str(os.getenv("AWS_REGION", "") or os.getenv("AWS_DEFAULT_REGION", "")).strip()
    if not secret_id or not region:
        return None
    try:
        import boto3
    except Exception:
        return None
    try:
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_id)
        raw = resp.get("SecretString")
        if not raw:
            secret_binary = resp.get("SecretBinary")
            if isinstance(secret_binary, (bytes, bytearray)):
                raw = secret_binary.decode("utf-8", errors="ignore")
            elif secret_binary not in (None, ""):
                raw = str(secret_binary)
        payload = _parse_json_mapping(raw)
        if not payload:
            return None
        value = payload.get(name)
        return None if value is None else str(value)
    except Exception:
        return None


def _get_from_vault(name: str) -> Optional[str]:
    vault_addr = str(os.getenv("VAULT_ADDR", "") or "").strip().rstrip("/")
    vault_token = str(os.getenv("VAULT_TOKEN", "") or "").strip()
    mount = str(os.getenv("VAULT_KV_MOUNT", "secret") or "secret").strip("/")
    path = str(os.getenv("VAULT_SECRET_PATH", "elara") or "elara").strip("/")
    if not vault_addr or not vault_token:
        return None
    try:
        import httpx
    except Exception:
        return None
    url = f"{vault_addr}/v1/{mount}/data/{path}"
    headers = {"X-Vault-Token": vault_token}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            payload = resp.json()
        data = (((payload or {}).get("data") or {}).get("data") or {})
        value = data.get(name)
        return None if value is None else str(value)
    except Exception:
        return None


@validate_call
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Resolve secret from configured provider with safe fallbacks.
    Provider order:
    1) env (always checked first)
    2) configured secret manager provider (file/aws/vault)
    """
    key = _normalize_name(name)
    if not key:
        return default

    raw = os.getenv(key)
    if raw not in (None, ""):
        return raw

    provider = str(os.getenv("SECRET_MANAGER_PROVIDER", "env") or "env").strip().lower()
    value = None
    if provider == "file":
        value = _get_from_file(key)
    elif provider == "aws":
        value = _get_from_aws(key)
    elif provider == "vault":
        value = _get_from_vault(key)

    if value not in (None, ""):
        return value
    return default


@validate_call
def evict_secrets_from_env(names: list[str]) -> int:
    removed = 0
    for raw in names or []:
        key = _normalize_name(raw)
        if not key:
            continue
        if key in os.environ:
            os.environ.pop(key, None)
            removed += 1
    return removed


def clear_secret_provider_cache():
    try:
        _file_secret_map.cache_clear()
    except Exception:
        pass

