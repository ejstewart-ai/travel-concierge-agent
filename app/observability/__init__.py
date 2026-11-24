"""Observability utilities: structured JSON logging, OpenTelemetry tracing, and PII redaction."""

from app.observability.logging import get_structured_logger, log_structured_event
from app.observability.pii_redactor import PiiRedactor, RedactionResult, get_pii_redactor
from app.observability.tracing import (
    configure_tracing,
    get_in_memory_span_exporter,
    get_trace_context_ids,
    get_tracer,
)

__all__ = [
    "PiiRedactor",
    "RedactionResult",
    "configure_tracing",
    "get_in_memory_span_exporter",
    "get_pii_redactor",
    "get_structured_logger",
    "get_trace_context_ids",
    "get_tracer",
    "log_structured_event",
]
