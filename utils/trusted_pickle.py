import base64
import hashlib
import hmac
import importlib
import io
import json
import logging
import math
import os
import pickle
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from config.settings import Config

log = logging.getLogger("elara.trusted_pickle")
_ARTIFACT_FORMAT = "elara-json-artifact-v1"

try:
    import numpy as np
except Exception:
    np = None

try:
    import pandas as pd
except Exception:
    pd = None


def _sig_path(path: str) -> str:
    return f"{path}.sig"


def _artifact_key_bytes() -> bytes:
    raw = str(getattr(Config, "ML_ARTIFACT_SIGNING_KEY", "") or "").strip()
    if not raw:
        raw = str(getattr(Config, "SECRET_KEY", "") or "").strip()
    return raw.encode("utf-8") if raw else b""


def _is_allowed_path(path: str) -> bool:
    if bool(getattr(Config, "ML_ARTIFACT_ALLOW_EXTERNAL_PATHS", True)):
        return True

    target = os.path.abspath(str(path or ""))
    root = os.path.abspath(str(getattr(Config, "root_path", os.getcwd()) or os.getcwd()))
    runtime = os.path.abspath(os.path.join(root, "runtime"))
    trusted_roots = [root, runtime]

    for base in trusted_roots:
        if target == base or target.startswith(base + os.sep):
            return True
    return False


def _artifact_signature_file(path: str, key: bytes) -> str:
    chunk_size = max(4096, int(getattr(Config, "ML_ARTIFACT_IO_CHUNK_SIZE", 1024 * 1024)))
    mac = hmac.new(key, digestmod=hashlib.sha256)
    mac.update(os.path.abspath(path).encode("utf-8", errors="ignore"))
    mac.update(b"\0")
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            mac.update(chunk)
    return mac.hexdigest()


def _allowed_class_prefixes():
    raw = str(getattr(Config, "ML_ARTIFACT_ALLOWED_CLASS_PREFIXES", "sklearn.") or "sklearn.")
    prefixes = tuple(part.strip() for part in raw.split(",") if part.strip())
    return prefixes or ("sklearn.",)


def _is_allowed_class(class_path: str) -> bool:
    target = str(class_path or "").strip()
    if not target:
        return False
    prefixes = _allowed_class_prefixes()
    return any(target.startswith(prefix) for prefix in prefixes)


def _class_path(obj: Any) -> str:
    cls = obj.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def _import_class(class_path: str):
    module_path, _, attr_path = str(class_path or "").rpartition(".")
    if not module_path or not attr_path:
        raise ValueError(f"Invalid class path: {class_path}")
    module = importlib.import_module(module_path)
    target = module
    for attr in attr_path.split("."):
        target = getattr(target, attr)
    return target


def _encode_ndarray(value):
    if np is None:
        raise TypeError("NumPy array serialization requested but numpy is unavailable.")
    buf = io.BytesIO()
    np.save(buf, value, allow_pickle=False)
    return {"__type__": "numpy.ndarray", "data_b64": base64.b64encode(buf.getvalue()).decode("ascii")}


def _decode_ndarray(marker):
    if np is None:
        raise ValueError("NumPy array deserialization requested but numpy is unavailable.")
    data = base64.b64decode(str(marker.get("data_b64") or "").encode("ascii"))
    return np.load(io.BytesIO(data), allow_pickle=False)


def _serialize_obj(value: Any):
    if value is None or isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"__type__": "float.nonfinite", "value": str(value)}

    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}

    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__type__": "time", "value": value.isoformat()}

    if isinstance(value, bytes):
        return {"__type__": "bytes", "value_b64": base64.b64encode(value).decode("ascii")}

    if np is not None:
        if isinstance(value, np.ndarray):
            return _encode_ndarray(value)
        if isinstance(value, np.generic):
            return {
                "__type__": "numpy.scalar",
                "dtype": str(value.dtype),
                "value": _serialize_obj(value.item()),
            }

    if pd is not None:
        if isinstance(value, pd.DataFrame):
            data_json = value.to_json(orient="split", date_format="iso", date_unit="ns")
            dtypes = {str(col): str(dtype) for col, dtype in value.dtypes.items()}
            return {"__type__": "pandas.DataFrame", "json": data_json, "dtypes": dtypes}
        if isinstance(value, pd.Series):
            data_json = value.to_json(orient="split", date_format="iso", date_unit="ns")
            return {
                "__type__": "pandas.Series",
                "json": data_json,
                "dtype": str(value.dtype),
                "name": value.name,
            }
        if isinstance(value, pd.Index):
            return {"__type__": "pandas.Index", "items": [_serialize_obj(v) for v in value.tolist()]}
        if isinstance(value, pd.Timestamp):
            return {"__type__": "pandas.Timestamp", "value": value.isoformat()}
        if isinstance(value, pd.Timedelta):
            return {"__type__": "pandas.Timedelta", "value": value.isoformat()}

    if isinstance(value, dict):
        return {
            "__type__": "dict",
            "items": [[_serialize_obj(k), _serialize_obj(v)] for k, v in value.items()],
        }

    if isinstance(value, list):
        return [_serialize_obj(item) for item in value]
    if isinstance(value, tuple):
        return {"__type__": "tuple", "items": [_serialize_obj(item) for item in value]}
    if isinstance(value, set):
        return {"__type__": "set", "items": [_serialize_obj(item) for item in sorted(value, key=str)]}

    class_path = _class_path(value)
    if hasattr(value, "__getstate__") and _is_allowed_class(class_path):
        marker = {
            "__type__": "py.object",
            "class_path": class_path,
            "state": _serialize_obj(value.__getstate__()),
        }
        # sklearn.tree._tree.Tree needs constructor args before __setstate__.
        if class_path == "sklearn.tree._tree.Tree":
            if np is None:
                raise TypeError("sklearn tree serialization requires numpy.")
            marker["ctor"] = {
                "n_features": int(getattr(value, "n_features", 0)),
                "n_outputs": int(getattr(value, "n_outputs", 1)),
                "n_classes": _serialize_obj(np.asarray(getattr(value, "n_classes", []), dtype=np.intp)),
            }
        return marker

    raise TypeError(
        f"Unsupported artifact object type: {type(value)}. "
        "Only JSON-safe, numpy/pandas, and allowed sklearn objects are supported."
    )


def _deserialize_obj(value):
    if isinstance(value, list):
        return [_deserialize_obj(item) for item in value]
    if not isinstance(value, dict):
        return value

    marker_type = value.get("__type__")
    if not marker_type:
        # Plain dict payload
        return {k: _deserialize_obj(v) for k, v in value.items()}

    if marker_type == "float.nonfinite":
        raw = str(value.get("value") or "").lower()
        if raw == "nan":
            return float("nan")
        if raw == "inf":
            return float("inf")
        if raw == "-inf":
            return float("-inf")
        raise ValueError(f"Invalid non-finite float marker: {raw}")

    if marker_type == "decimal":
        return Decimal(str(value.get("value") or "0"))
    if marker_type == "datetime":
        return datetime.fromisoformat(str(value.get("value") or ""))
    if marker_type == "date":
        return date.fromisoformat(str(value.get("value") or ""))
    if marker_type == "time":
        return time.fromisoformat(str(value.get("value") or ""))
    if marker_type == "bytes":
        return base64.b64decode(str(value.get("value_b64") or "").encode("ascii"))
    if marker_type == "numpy.ndarray":
        return _decode_ndarray(value)
    if marker_type == "numpy.scalar":
        if np is None:
            raise ValueError("numpy.scalar encountered but numpy is unavailable.")
        dtype = np.dtype(str(value.get("dtype") or "float64"))
        parsed = _deserialize_obj(value.get("value"))
        return np.asarray(parsed, dtype=dtype).item()

    if marker_type == "pandas.DataFrame":
        if pd is None:
            raise ValueError("pandas.DataFrame encountered but pandas is unavailable.")
        frame = pd.read_json(io.StringIO(str(value.get("json") or "{}")), orient="split")
        dtypes = value.get("dtypes") or {}
        for col_name, dtype_name in dtypes.items():
            if col_name in frame.columns:
                try:
                    frame[col_name] = frame[col_name].astype(dtype_name)
                except Exception:
                    continue
        return frame

    if marker_type == "pandas.Series":
        if pd is None:
            raise ValueError("pandas.Series encountered but pandas is unavailable.")
        series = pd.read_json(io.StringIO(str(value.get("json") or "{}")), typ="series", orient="split")
        try:
            dtype_name = str(value.get("dtype") or "")
            if dtype_name:
                series = series.astype(dtype_name)
        except Exception:
            pass
        series.name = value.get("name")
        return series

    if marker_type == "pandas.Index":
        if pd is None:
            raise ValueError("pandas.Index encountered but pandas is unavailable.")
        return pd.Index([_deserialize_obj(item) for item in value.get("items", [])])

    if marker_type == "pandas.Timestamp":
        if pd is None:
            raise ValueError("pandas.Timestamp encountered but pandas is unavailable.")
        return pd.Timestamp(str(value.get("value") or ""))

    if marker_type == "pandas.Timedelta":
        if pd is None:
            raise ValueError("pandas.Timedelta encountered but pandas is unavailable.")
        return pd.Timedelta(str(value.get("value") or ""))

    if marker_type == "dict":
        out = {}
        for item in value.get("items", []):
            if not isinstance(item, list) or len(item) != 2:
                continue
            key = _deserialize_obj(item[0])
            out[key] = _deserialize_obj(item[1])
        return out

    if marker_type == "tuple":
        return tuple(_deserialize_obj(item) for item in value.get("items", []))
    if marker_type == "set":
        return set(_deserialize_obj(item) for item in value.get("items", []))

    if marker_type == "py.object":
        class_path = str(value.get("class_path") or "").strip()
        if not _is_allowed_class(class_path):
            raise ValueError(f"Disallowed object class during artifact load: {class_path}")
        cls = _import_class(class_path)
        ctor = value.get("ctor") or {}

        if class_path == "sklearn.tree._tree.Tree":
            if np is None:
                raise ValueError("sklearn tree artifact requires numpy.")
            n_features = int(ctor.get("n_features", 0))
            n_outputs = int(ctor.get("n_outputs", 1))
            n_classes = _deserialize_obj(ctor.get("n_classes"))
            n_classes = np.asarray(n_classes, dtype=np.intp)
            instance = cls(n_features, n_classes, n_outputs)
        else:
            try:
                instance = cls.__new__(cls)
            except Exception as e:
                raise ValueError(f"Unable to create object for class {class_path}: {e}") from e

        state = _deserialize_obj(value.get("state"))
        setstate = getattr(instance, "__setstate__", None)
        if callable(setstate):
            setstate(state)
        else:
            if not isinstance(state, dict):
                raise ValueError(f"Object state for {class_path} must be dict when __setstate__ is absent.")
            instance.__dict__.update(state)
        return instance

    raise ValueError(f"Unknown artifact marker type: {marker_type}")


def dump_pickle_artifact(obj, path: str, *, purpose: str = "artifact") -> bool:
    """
    Backward-compatible API name.
    Actual storage format is signed JSON (no pickle) for safer artifact handling.
    """
    target = os.path.abspath(str(path or ""))
    if not target:
        raise ValueError(f"Invalid {purpose} path.")
    if not _is_allowed_path(target):
        raise ValueError(f"{purpose} path not allowed: {target}")

    artifact_doc = {
        "format": _ARTIFACT_FORMAT,
        "purpose": str(purpose or "artifact"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": _serialize_obj(obj),
    }
    payload = json.dumps(
        artifact_doc,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "wb") as f:
        f.write(payload)

    key = _artifact_key_bytes()
    if key:
        sig = _artifact_signature_file(target, key)
        with open(_sig_path(target), "w", encoding="utf-8") as f:
            f.write(sig)
    return True


def load_pickle_artifact(path: str, *, purpose: str = "artifact"):
    """
    Backward-compatible API name.
    Loads signed JSON artifacts. Legacy pickle can be optionally enabled for migration only.
    """
    target = os.path.abspath(str(path or ""))
    if not os.path.exists(target):
        raise FileNotFoundError(f"{purpose} not found: {target}")
    if not _is_allowed_path(target):
        raise ValueError(f"{purpose} path not allowed: {target}")

    max_bytes = max(1024, int(getattr(Config, "ML_ARTIFACT_MAX_BYTES", 100 * 1024 * 1024)))
    file_size = int(os.path.getsize(target))
    if file_size > max_bytes:
        raise ValueError(f"{purpose} too large ({file_size} bytes > max {max_bytes}).")

    require_signed = bool(getattr(Config, "ML_REQUIRE_SIGNED_ARTIFACTS", False))
    key = _artifact_key_bytes()
    sig_file = _sig_path(target)

    if require_signed and not key:
        raise ValueError(
            f"{purpose} signing key missing while ML_REQUIRE_SIGNED_ARTIFACTS=1. "
            "Set ML_ARTIFACT_SIGNING_KEY or SECRET_KEY."
        )

    if key:
        if not os.path.exists(sig_file):
            if require_signed:
                raise ValueError(f"{purpose} signature missing: {sig_file}")
            log.warning("%s signature missing, loading unsigned artifact in dev mode: %s", purpose, target)
        else:
            with open(sig_file, "r", encoding="utf-8") as f:
                expected = str(f.read() or "").strip().lower()
            actual = _artifact_signature_file(target, key)
            if not expected or not hmac.compare_digest(expected, actual):
                raise ValueError(f"{purpose} signature mismatch: {target}")
    elif require_signed:
        raise ValueError(f"{purpose} signature verification required but signing key unavailable.")

    with open(target, "rb") as f:
        raw = f.read()

    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        allow_legacy_pickle = bool(getattr(Config, "ML_ARTIFACT_ALLOW_LEGACY_PICKLE", False))
        if allow_legacy_pickle:
            log.warning(
                "Legacy pickle artifact fallback enabled for %s (%s). "
                "Set ML_ARTIFACT_ALLOW_LEGACY_PICKLE=0 in production.",
                target,
                purpose,
            )
            return pickle.loads(raw)
        raise ValueError(
            f"{purpose} is not a JSON artifact. "
            "Legacy pickle loading is disabled (ML_ARTIFACT_ALLOW_LEGACY_PICKLE=0)."
        ) from e

    if str(doc.get("format") or "") != _ARTIFACT_FORMAT:
        raise ValueError(f"{purpose} unsupported artifact format: {doc.get('format')}")

    payload = doc.get("payload")
    return _deserialize_obj(payload)

