"""
OpenTelemetry tracing bootstrap helpers for Flask/FastAPI runtimes.
"""

import logging
import os
from typing import Dict

log = logging.getLogger("elara.opentelemetry")
_PROVIDER_READY = False


def _parse_otlp_headers(raw: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    text = str(raw or "").strip()
    if not text:
        return headers
    for pair in text.split(","):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            headers[key] = value
    return headers


def _otel_enabled() -> bool:
    enabled = str(os.getenv("OTEL_ENABLED", "") or "").strip().lower()
    if enabled in {"1", "true", "yes", "on"}:
        return True
    if str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip():
        return True
    console_enabled = str(os.getenv("OTEL_EXPORTER_CONSOLE", "0")).strip().lower()
    return console_enabled in {"1", "true", "yes", "on"}


def _ensure_provider(service_name: str) -> bool:
    global _PROVIDER_READY
    if _PROVIDER_READY:
        return True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        log.warning(
            "OpenTelemetry packages not installed; tracing disabled. "
            "(install opentelemetry-api/opentelemetry-sdk/exporter/instrumentations)"
        )
        return False

    endpoint = str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip()
    headers = _parse_otlp_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""))
    console_enabled = str(os.getenv("OTEL_EXPORTER_CONSOLE", "0")).strip() in {"1", "true", "yes"}

    try:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers or None)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        if console_enabled or not endpoint:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _PROVIDER_READY = True
        log.info("OpenTelemetry provider ready (service=%s endpoint=%s).", service_name, endpoint or "console")
        return True
    except Exception as e:
        # If tracer provider already exists, continue without failing startup.
        text = str(e or "").lower()
        if "overriding of current tracerprovider is not allowed" in text:
            _PROVIDER_READY = True
            return True
        log.error("Failed to initialize OpenTelemetry provider: %s", e)
        return False


def _instrument_common_http():
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass


def install_flask_tracing(app, service_name: str = "elara-core"):
    if not _ensure_provider(service_name):
        return
    try:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor

        FlaskInstrumentor().instrument_app(app)
        _instrument_common_http()

        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            from database.models import db

            with app.app_context():
                SQLAlchemyInstrumentor().instrument(engine=db.engine)
        except Exception as db_err:
            log.warning("SQLAlchemy OTEL instrumentation skipped: %s", db_err)

        log.info("OpenTelemetry tracing installed for Flask app.")
    except Exception as e:
        log.error("Failed installing Flask tracing: %s", e)


def install_fastapi_tracing(app, service_name: str = "elara-webhook-fastapi"):
    if not _ensure_provider(service_name):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        _instrument_common_http()
        log.info("OpenTelemetry tracing installed for FastAPI app.")
    except Exception as e:
        log.error("Failed installing FastAPI tracing: %s", e)


def install_background_tracing(service_name: str = "elara-background"):
    if not _otel_enabled():
        return
    if not _ensure_provider(service_name):
        return
    _instrument_common_http()
    log.info("OpenTelemetry tracing installed for background service.")

