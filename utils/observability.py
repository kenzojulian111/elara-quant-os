from __future__ import annotations

import contextlib
import contextvars
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


_LABEL_RE = re.compile(r"[^a-zA-Z0-9_]")
_OBS_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "elara_observability_context",
    default={},
)


def _normalize_metric_name(value: str) -> str:
    text = _LABEL_RE.sub("_", str(value or "").strip())
    if not text:
        return "unnamed"
    if text[0].isdigit():
        text = "_" + text
    return text


def _normalize_label_value(value: Any) -> str:
    return str(value if value is not None else "").replace("\\", "\\\\").replace("\n", " ").replace('"', '\\"')


def _labels_key(labels: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in (labels or {}).items()))


def _labels_text(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    parts = [f'{_normalize_metric_name(key)}="{_normalize_label_value(value)}"' for key, value in labels]
    return "{" + ",".join(parts) + "}"


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_observability_context() -> dict[str, str]:
    return dict(_OBS_CONTEXT.get() or {})


def set_observability_context(**fields: Any):
    clean = {
        str(key): str(value)
        for key, value in fields.items()
        if str(key or "").strip() and str(value or "").strip()
    }
    return _OBS_CONTEXT.set(clean)


def reset_observability_context(token) -> None:
    if token is not None:
        _OBS_CONTEXT.reset(token)


@dataclass
class HistogramState:
    buckets: tuple[float, ...]
    counts: dict[tuple[tuple[str, str], ...], list[int]] = field(default_factory=dict)
    sums: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    totals: dict[tuple[tuple[str, str], ...], int] = field(default_factory=dict)


class MetricsRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
        self._histograms: dict[str, HistogramState] = {}
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}

    def counter(self, name: str, value: float = 1.0, **labels: Any) -> None:
        metric = _normalize_metric_name(name)
        key = _labels_key(labels)
        with self._lock:
            self._types.setdefault(metric, "counter")
            self._help.setdefault(metric, metric.replace("_", " "))
            bucket = self._counters.setdefault(metric, {})
            bucket[key] = float(bucket.get(key, 0.0)) + float(value)

    def gauge(self, name: str, value: float, **labels: Any) -> None:
        metric = _normalize_metric_name(name)
        key = _labels_key(labels)
        with self._lock:
            self._types.setdefault(metric, "gauge")
            self._help.setdefault(metric, metric.replace("_", " "))
            self._gauges.setdefault(metric, {})[key] = float(value)

    def histogram(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[float, ...] | None = None,
        **labels: Any,
    ) -> None:
        metric = _normalize_metric_name(name)
        resolved_buckets = tuple(sorted(float(item) for item in (buckets or (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))))
        key = _labels_key(labels)
        sample = float(value)
        with self._lock:
            self._types.setdefault(metric, "histogram")
            self._help.setdefault(metric, metric.replace("_", " "))
            state = self._histograms.setdefault(metric, HistogramState(buckets=resolved_buckets))
            if state.buckets != resolved_buckets:
                state.buckets = tuple(sorted(set((*state.buckets, *resolved_buckets))))
            counts = state.counts.setdefault(key, [0 for _ in state.buckets])
            if len(counts) != len(state.buckets):
                counts.extend([0 for _ in range(len(state.buckets) - len(counts))])
            for idx, upper_bound in enumerate(state.buckets):
                if sample <= upper_bound:
                    counts[idx] += 1
            state.sums[key] = float(state.sums.get(key, 0.0)) + sample
            state.totals[key] = int(state.totals.get(key, 0)) + 1

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for metric in sorted(set((*self._counters.keys(), *self._gauges.keys(), *self._histograms.keys()))):
                metric_type = self._types.get(metric, "gauge")
                lines.append(f"# HELP {metric} {self._help.get(metric, metric)}")
                lines.append(f"# TYPE {metric} {metric_type}")
                if metric_type == "counter":
                    for labels, value in sorted(self._counters.get(metric, {}).items()):
                        lines.append(f"{metric}{_labels_text(labels)} {value:g}")
                elif metric_type == "gauge":
                    for labels, value in sorted(self._gauges.get(metric, {}).items()):
                        lines.append(f"{metric}{_labels_text(labels)} {value:g}")
                elif metric_type == "histogram":
                    state = self._histograms.get(metric)
                    if not state:
                        continue
                    for labels, counts in sorted(state.counts.items()):
                        for upper_bound, count in zip(state.buckets, counts):
                            le_labels = tuple(sorted((*labels, ("le", f"{upper_bound:g}"))))
                            lines.append(f"{metric}_bucket{_labels_text(le_labels)} {count:g}")
                        inf_labels = tuple(sorted((*labels, ("le", "+Inf"))))
                        lines.append(f"{metric}_bucket{_labels_text(inf_labels)} {state.totals.get(labels, 0):g}")
                        lines.append(f"{metric}_sum{_labels_text(labels)} {state.sums.get(labels, 0.0):g}")
                        lines.append(f"{metric}_count{_labels_text(labels)} {state.totals.get(labels, 0):g}")
        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()


def record_http_request(method: str, path: str, status_code: int, duration_sec: float) -> None:
    labels = {
        "method": str(method or "GET").upper(),
        "path": str(path or "/")[:160],
        "status": str(int(status_code or 0)),
    }
    REGISTRY.counter("elara_http_requests_total", 1, **labels)
    REGISTRY.histogram(
        "elara_http_request_duration_seconds",
        max(0.0, float(duration_sec or 0.0)),
        buckets=None,
        **labels,
    )


def record_runtime_health(metrics: dict[str, Any] | None) -> None:
    data = dict(metrics or {})
    numeric_gauges = {
        "cpu_pct": "elara_runtime_cpu_pct",
        "mem_pct": "elara_runtime_mem_pct",
        "loop_lag_ms": "elara_runtime_loop_lag_ms",
        "db_lock_rate_per_min": "elara_db_lock_rate_per_min",
        "db_write_lock_contention_rate_per_min": "elara_db_write_lock_contention_rate_per_min",
        "firewall_queue_depth": "elara_firewall_queue_depth",
    }
    for source_key, metric_name in numeric_gauges.items():
        value = data.get(source_key)
        if isinstance(value, (int, float)) and not math.isnan(float(value)):
            REGISTRY.gauge(metric_name, float(value))
    runtime_state = data.get("runtime_state")
    if isinstance(runtime_state, dict):
        REGISTRY.gauge(
            "elara_runtime_state",
            1,
            state=str(runtime_state.get("state") or "unknown"),
        )
    sanity = data.get("runtime_sanity")
    if isinstance(sanity, dict):
        REGISTRY.gauge(
            "elara_runtime_sanity_score",
            float(sanity.get("score") or 0),
            status=str(sanity.get("status") or "unknown"),
        )


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return float(default)
        return result
    except Exception:
        return float(default)


def _timestamp_to_epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def record_trader_status(
    payload: dict[str, Any] | None,
    *,
    source: str = "trader",
    stale_after_sec: float = 30.0,
) -> None:
    data = dict(payload or {})
    labels = {"source": str(source or "trader")}
    REGISTRY.gauge("elara_trader_status_present", 1.0 if data else 0.0, **labels)
    if not data:
        return

    bool_gauges = {
        "is_running": "elara_trader_running",
        "setup_in_progress": "elara_trader_setup_in_progress",
        "simulation_fallback": "elara_trader_simulation_fallback",
        "lite_mode": "elara_trader_lite_mode",
        "life_blocked": "elara_trader_life_blocked",
        "daily_risk_blocked": "elara_trader_daily_risk_blocked",
        "global_risk_blocked": "elara_trader_global_risk_blocked",
        "exchange_time_drift_high": "elara_trader_exchange_time_drift_high",
    }
    for source_key, metric_name in bool_gauges.items():
        REGISTRY.gauge(metric_name, 1.0 if bool(data.get(source_key, False)) else 0.0, **labels)

    numeric_gauges = {
        "open_positions": "elara_trader_open_positions",
        "equity": "elara_trader_equity",
        "cash": "elara_trader_cash",
        "ram_usage_pct": "elara_trader_ram_usage_pct",
        "daily_drawdown_pct": "elara_trader_daily_drawdown_pct",
        "global_drawdown_pct": "elara_trader_global_drawdown_pct",
        "max_drawdown_limit_pct": "elara_trader_max_drawdown_limit_pct",
    }
    for source_key, metric_name in numeric_gauges.items():
        if source_key in data:
            REGISTRY.gauge(metric_name, _to_float(data.get(source_key)), **labels)

    state = str(data.get("control_state") or "unknown").strip().lower() or "unknown"
    REGISTRY.gauge("elara_trader_control_state", 1.0, state=state, **labels)

    ts_epoch = _timestamp_to_epoch(data.get("ts"))
    if ts_epoch is not None:
        age_sec = max(0.0, time.time() - ts_epoch)
        REGISTRY.gauge("elara_trader_status_age_seconds", age_sec, **labels)
        REGISTRY.gauge(
            "elara_trader_status_stale",
            1.0 if age_sec > max(0.0, float(stale_after_sec or 0.0)) else 0.0,
            **labels,
        )


def record_feed_freshness(
    feed_name: str,
    last_update_ts: Any,
    *,
    symbol: str = "",
    stale_after_sec: float = 30.0,
) -> None:
    ts_epoch = _timestamp_to_epoch(last_update_ts)
    labels = {
        "feed": str(feed_name or "unknown"),
        "symbol": str(symbol or "all"),
    }
    if ts_epoch is None:
        REGISTRY.gauge("elara_feed_freshness_present", 0.0, **labels)
        REGISTRY.gauge("elara_feed_stale", 1.0, **labels)
        return
    age_sec = max(0.0, time.time() - ts_epoch)
    REGISTRY.gauge("elara_feed_freshness_present", 1.0, **labels)
    REGISTRY.gauge("elara_feed_age_seconds", age_sec, **labels)
    REGISTRY.gauge(
        "elara_feed_stale",
        1.0 if age_sec > max(0.0, float(stale_after_sec or 0.0)) else 0.0,
        **labels,
    )


def record_exchange_latency(
    *,
    exchange: str,
    operation: str,
    symbol: str,
    duration_sec: float,
    status: str,
) -> None:
    labels = {
        "exchange": str(exchange or "unknown"),
        "operation": str(operation or "unknown"),
        "symbol": str(symbol or "unknown"),
        "status": str(status or "unknown"),
    }
    REGISTRY.histogram(
        "elara_exchange_operation_duration_seconds",
        max(0.0, float(duration_sec or 0.0)),
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
        **labels,
    )
    REGISTRY.counter("elara_exchange_operations_total", 1, **labels)


def record_exchange_order_event(
    *,
    exchange: str,
    symbol: str,
    side: str,
    status: str,
) -> None:
    REGISTRY.counter(
        "elara_exchange_order_events_total",
        1,
        exchange=str(exchange or "unknown"),
        symbol=str(symbol or "unknown"),
        side=str(side or "unknown"),
        status=str(status or "unknown"),
    )


@contextlib.contextmanager
def profile_block(name: str, **labels: Any) -> Iterator[None]:
    started = time.perf_counter()
    failed = False
    try:
        yield
    except Exception:
        failed = True
        raise
    finally:
        duration = max(0.0, time.perf_counter() - started)
        block = _normalize_metric_name(name)
        REGISTRY.histogram(
            "elara_profile_block_duration_seconds",
            duration,
            buckets=None,
            block=block,
            status="error" if failed else "ok",
            **labels,
        )
        REGISTRY.counter(
            "elara_profile_block_total",
            1,
            block=block,
            status="error" if failed else "ok",
            **labels,
        )
