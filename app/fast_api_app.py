"""FastAPI production server entrypoint for Cloud Run and local ADK API serving.

Wires ADK's FastAPI application with:
- Persistent `DatabaseSessionService` (SQLite locally, Cloud SQL PostgreSQL on Cloud Run)
- Automatic OpenTelemetry export (`otel_to_cloud=True` when running on Google Cloud)
- Health check endpoint for Cloud Run readiness/liveness probes
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.adk.cli.fast_api import get_fast_api_app

from app.config import get_settings
from app.observability.logging import get_structured_logger, log_structured_event
from app.observability.tracing import configure_tracing

_logger = get_structured_logger(__name__)
_settings = get_settings()

configure_tracing(
    service_name=_settings.otel_service_name,
    exporter_mode=_settings.otel_exporter_mode,
    project_id=_settings.google_cloud_project,
)

# Parent directory containing the `app/` ADK package.
_AGENTS_DIR = str(Path(__file__).resolve().parent.parent)
_OTEL_TO_CLOUD = _settings.otel_exporter_mode.lower() == "cloud_trace" and bool(
    _settings.google_cloud_project
)

# Ensure SQLite parent directory exists before ADK initializes DatabaseSessionService.
if _settings.session_database_url.startswith("sqlite"):
    _db_file = _settings.session_database_url.split("///", maxsplit=1)[-1]
    if _db_file and _db_file != ":memory:":
        Path(_db_file).parent.mkdir(parents=True, exist_ok=True)

app = get_fast_api_app(
    agents_dir=_AGENTS_DIR,
    session_service_uri=_settings.session_database_url,
    web=True,
    otel_to_cloud=_OTEL_TO_CLOUD,
)


@app.get("/healthz")
async def health_check() -> dict[str, str]:
    """Liveness and readiness probe endpoint for Cloud Run."""
    return {
        "status": "ok",
        "service": _settings.otel_service_name,
        "fast_model": _settings.models.fast_model,
        "reasoning_model": _settings.models.reasoning_model,
    }


log_structured_event(
    _logger,
    logging.INFO,
    "fastapi_initialized",
    "Travel Concierge FastAPI server initialized.",
    payload={
        "otel_to_cloud": _OTEL_TO_CLOUD,
        "environment": _settings.environment,
    },
)
