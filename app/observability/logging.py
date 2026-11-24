"""Structured JSON logging integrated with PII redaction and OpenTelemetry traces.

Emits single-line JSON log records compatible with Google Cloud Logging so every
agent step, intent, outcome, and guardrail action is searchable without raw
``print()`` statements.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from app.observability.pii_redactor import PiiRedactor, get_pii_redactor
from app.observability.tracing import get_trace_context_ids


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as Google Cloud Logging-compatible JSON with PII scrubbing."""

    def __init__(self, redactor: PiiRedactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or get_pii_redactor()

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = get_trace_context_ids()
        raw_message = record.getMessage()
        redacted_msg = self._redactor.redact_text(raw_message)

        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": redacted_msg.redacted_text,
            "event_type": getattr(record, "event_type", "application_log"),
        }

        if trace_id:
            log_entry["trace_id"] = trace_id
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
            if project_id:
                log_entry["logging.googleapis.com/trace"] = (
                    f"projects/{project_id}/traces/{trace_id}"
                )
        if span_id:
            log_entry["span_id"] = span_id
            log_entry["logging.googleapis.com/spanId"] = span_id

        for attr in ("agent_name", "session_id", "user_id", "audit_id", "phase"):
            val = getattr(record, attr, None)
            if val is not None:
                log_entry[attr] = self._redactor.redact_data(val)

        payload = getattr(record, "payload", None)
        if payload is not None:
            log_entry["payload"] = self._redactor.redact_data(payload)

        if redacted_msg.had_pii:
            log_entry["pii_redacted_types"] = list(redacted_msg.findings)

        if record.exc_info:
            log_entry["exception"] = self._redactor.redact_text(
                self.formatException(record.exc_info)
            ).redacted_text

        return json.dumps(log_entry, default=str, ensure_ascii=False)


def get_structured_logger(name: str) -> logging.Logger:
    """Returns a logger configured with a single StructuredJsonFormatter handler."""
    logger = logging.getLogger(name)
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    has_json_handler = any(
        isinstance(handler.formatter, StructuredJsonFormatter)
        for handler in logger.handlers
    )
    if not has_json_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger


def log_structured_event(
    logger: logging.Logger,
    level: int,
    event_type: str,
    message: str,
    *,
    agent_name: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    audit_id: str | None = None,
    phase: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Emits a structured JSON log record with rich AgentOps metadata."""
    logger.log(
        level,
        message,
        extra={
            "event_type": event_type,
            "agent_name": agent_name,
            "session_id": session_id,
            "user_id": user_id,
            "audit_id": audit_id,
            "phase": phase,
            "payload": payload or {},
        },
    )
