"""OpenTelemetry distributed tracing setup for the Travel Concierge Agent.

Links spans across incoming user queries, multi-agent delegation, model calls,
and tool executions. Supports exporting to Google Cloud Trace in production or
capturing spans in memory for automated verification and local development.
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_TRACING_CONFIGURED = False
_IN_MEMORY_EXPORTER: InMemorySpanExporter | None = None


def configure_tracing(
    *,
    service_name: str | None = None,
    exporter_mode: str | None = None,
    project_id: str | None = None,
    force_reconfigure: bool = False,
) -> TracerProvider:
    """Initializes the global OpenTelemetry TracerProvider.

    Args:
        service_name: Logical service name attached to every span resource.
        exporter_mode: One of ``cloud_trace``, ``memory``, or ``noop``.
        project_id: Optional Google Cloud project ID for Cloud Trace export.
        force_reconfigure: When True, attaches a fresh processor even if already
            configured (useful in unit tests).

    Returns:
        The active OpenTelemetry ``TracerProvider``.
    """
    global _TRACING_CONFIGURED, _IN_MEMORY_EXPORTER

    resolved_service = service_name or os.environ.get(
        "OTEL_SERVICE_NAME", "travel-concierge-agent"
    )
    resolved_mode = (
        exporter_mode or os.environ.get("OTEL_EXPORTER_MODE", "memory")
    ).lower()
    resolved_project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")

    existing_provider = trace.get_tracer_provider()
    if _TRACING_CONFIGURED and not force_reconfigure and isinstance(
        existing_provider, TracerProvider
    ):
        return existing_provider

    resource = Resource.create(
        {
            "service.name": resolved_service,
            "service.version": "0.1.0",
            "deployment.environment": os.environ.get("ENVIRONMENT", "development"),
        }
    )

    if isinstance(existing_provider, TracerProvider):
        provider = existing_provider
    else:
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

    if resolved_mode == "cloud_trace" and resolved_project:
        exporter = _build_cloud_trace_exporter(resolved_project)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        else:
            _attach_in_memory_exporter(provider)
    elif resolved_mode == "memory":
        _attach_in_memory_exporter(provider)

    _TRACING_CONFIGURED = True
    return provider


def _attach_in_memory_exporter(provider: TracerProvider) -> None:
    global _IN_MEMORY_EXPORTER
    if _IN_MEMORY_EXPORTER is None:
        _IN_MEMORY_EXPORTER = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_IN_MEMORY_EXPORTER))


def _build_cloud_trace_exporter(project_id: str) -> SpanExporter | None:
    try:
        from opentelemetry.exporter.cloud_trace import (  # type: ignore[import-untyped]
            CloudTraceSpanExporter,
        )

        return CloudTraceSpanExporter(project_id=project_id)
    except Exception:
        return None


def get_in_memory_span_exporter() -> InMemorySpanExporter | None:
    """Returns the in-memory span exporter if tracing was configured in memory mode."""
    return _IN_MEMORY_EXPORTER


def get_tracer(name: str = "travel_concierge_agent") -> trace.Tracer:
    """Returns an OpenTelemetry Tracer, configuring the provider on first access."""
    if not _TRACING_CONFIGURED:
        configure_tracing()
    return trace.get_tracer(name)


def get_trace_context_ids() -> tuple[str | None, str | None]:
    """Returns the active OpenTelemetry ``(trace_id, span_id)`` hex strings, if any."""
    span = trace.get_current_span()
    span_context: Any = span.get_span_context() if span is not None else None
    if span_context is None or not span_context.is_valid:
        return None, None
    return (
        f"{span_context.trace_id:032x}",
        f"{span_context.span_id:016x}",
    )
