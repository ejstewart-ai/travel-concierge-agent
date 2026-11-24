"""Unit tests for PII redaction, structured JSON logging, and OpenTelemetry tracing."""

from __future__ import annotations

import json
import logging

from app.observability.logging import StructuredJsonFormatter
from app.observability.pii_redactor import PiiRedactor
from app.observability.tracing import (
    configure_tracing,
    get_in_memory_span_exporter,
    get_trace_context_ids,
    get_tracer,
)


def test_pii_redactor_scrubs_all_sensitive_patterns() -> None:
    redactor = PiiRedactor(enable_cloud_dlp=False)
    raw = (
        "Email alice@example.com, phone (415) 555-0192, card 4111-2222-3333-4444, "
        "SSN 123-45-6789, passport: AB1234567."
    )
    result = redactor.redact_text(raw)
    assert result.had_pii is True
    assert "alice@example.com" not in result.redacted_text
    assert "555-0192" not in result.redacted_text
    assert "4111-2222-3333-4444" not in result.redacted_text
    assert "123-45-6789" not in result.redacted_text
    assert "AB1234567" not in result.redacted_text
    assert "[REDACTED_EMAIL]" in result.redacted_text
    assert "[REDACTED_PHONE]" in result.redacted_text
    assert "[REDACTED_CREDIT_CARD]" in result.redacted_text
    assert "[REDACTED_SSN]" in result.redacted_text
    assert "[REDACTED_PASSPORT]" in result.redacted_text


def test_pii_redactor_scrubs_nested_sensitive_keys() -> None:
    redactor = PiiRedactor(enable_cloud_dlp=False)
    payload = {
        "traveler": "Alex",
        "api_key": "super-secret-value",
        "contact": {"email": "alex@example.com"},
    }
    scrubbed = redactor.redact_data(payload)
    assert scrubbed["api_key"] == "[REDACTED_FIELD]"
    assert scrubbed["contact"]["email"] == "[REDACTED_EMAIL]"


def test_structured_json_formatter_emits_valid_json_with_trace_ids() -> None:
    configure_tracing(exporter_mode="memory", force_reconfigure=True)
    tracer = get_tracer("test_logging_tracer")
    formatter = StructuredJsonFormatter(redactor=PiiRedactor(enable_cloud_dlp=False))

    with tracer.start_as_current_span("test.span.root"):
        trace_id, span_id = get_trace_context_ids()
        assert trace_id is not None
        assert span_id is not None

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Booking lookup for user@example.com",
            args=(),
            exc_info=None,
        )
        record.event_type = "tool_intent"
        record.agent_name = "flight_research_agent"
        record.phase = "INTENT"
        record.payload = {"origin_iata": "SFO"}

        rendered = formatter.format(record)

    parsed = json.loads(rendered)
    assert parsed["severity"] == "INFO"
    assert parsed["event_type"] == "tool_intent"
    assert parsed["phase"] == "INTENT"
    assert parsed["trace_id"] == trace_id
    assert parsed["span_id"] == span_id
    assert "user@example.com" not in parsed["message"]
    assert "[REDACTED_EMAIL]" in parsed["message"]

    exporter = get_in_memory_span_exporter()
    assert exporter is not None
    finished_names = [span.name for span in exporter.get_finished_spans()]
    assert "test.span.root" in finished_names
