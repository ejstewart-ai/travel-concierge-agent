"""Active PII redaction pipeline for logs, guardrails, and persistent memory.

Supports Google Cloud Sensitive Data Protection (Cloud DLP API) when enabled on
Google Cloud, paired with a deterministic local regex redactor so sensitive data
is scrubbed consistently in local development, CI tests, and production.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# Ordered from most specific to most general to prevent partial overlapping masks.
_LOCAL_PII_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "CREDIT_CARD_NUMBER",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "[REDACTED_CREDIT_CARD]",
    ),
    (
        "US_SOCIAL_SECURITY_NUMBER",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    (
        "EMAIL_ADDRESS",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    (
        "PHONE_NUMBER",
        re.compile(
            r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}\b"
        ),
        "[REDACTED_PHONE]",
    ),
    (
        "PASSPORT_NUMBER",
        re.compile(r"\b(?:passport(?:\s*(?:no|num|number|#))?[:\s#-]*)([A-Z]{1,2}\d{6,9})\b", re.I),
        "passport: [REDACTED_PASSPORT]",
    ),
    (
        "API_KEY_OR_TOKEN",
        re.compile(r"\b(?:AIza[0-9A-Za-z\-_]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})\b"),
        "[REDACTED_SECRET]",
    ),
)

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "credit_card",
        "cvv",
        "password",
        "passport",
        "passport_number",
        "payment_token",
        "secret",
        "ssn",
        "token",
    }
)


@dataclass(frozen=True)
class RedactionResult:
    """Result of scanning and redacting a text payload."""

    redacted_text: str
    findings: tuple[str, ...] = field(default_factory=tuple)
    backend: str = "local_regex"

    @property
    def had_pii(self) -> bool:
        """Returns True if one or more PII patterns were scrubbed."""
        return len(self.findings) > 0


class PiiRedactor:
    """Scrubs sensitive PII from text and nested JSON-like structures."""

    def __init__(
        self,
        *,
        enable_cloud_dlp: bool = False,
        project_id: str | None = None,
    ) -> None:
        self.enable_cloud_dlp = enable_cloud_dlp and bool(project_id)
        self.project_id = project_id
        self._dlp_client: Any = None

    def _get_dlp_client(self) -> Any:
        if self._dlp_client is None and self.enable_cloud_dlp:
            try:
                from google.cloud import dlp_v2  # type: ignore[import-untyped]

                self._dlp_client = dlp_v2.DlpServiceClient()
            except Exception:
                # Fall back seamlessly to local scrubbing when DLP SDK/creds are unavailable.
                self.enable_cloud_dlp = False
        return self._dlp_client

    def _redact_with_cloud_dlp(self, text: str) -> RedactionResult | None:
        client = self._get_dlp_client()
        if client is None or not self.project_id:
            return None
        try:
            parent = f"projects/{self.project_id}"
            info_types = [
                {"name": "EMAIL_ADDRESS"},
                {"name": "PHONE_NUMBER"},
                {"name": "CREDIT_CARD_NUMBER"},
                {"name": "US_SOCIAL_SECURITY_NUMBER"},
                {"name": "PASSPORT"},
            ]
            response = client.deidentify_content(
                request={
                    "parent": parent,
                    "deidentify_config": {
                        "info_type_transformations": {
                            "transformations": [
                                {
                                    "primitive_transformation": {
                                        "replace_with_info_type_config": {}
                                    }
                                }
                            ]
                        }
                    },
                    "inspect_config": {"info_types": info_types},
                    "item": {"value": text},
                }
            )
            redacted = response.item.value
            findings = tuple(
                summary.info_type.name
                for summary in response.overview.transformation_summaries
                if summary.info_type and summary.info_type.name
            )
            return RedactionResult(
                redacted_text=redacted,
                findings=findings,
                backend="google_cloud_dlp",
            )
        except Exception:
            return None

    def redact_text(self, text: str) -> RedactionResult:
        """Scrubs PII from a string via Cloud DLP (if enabled) and local regex patterns."""
        if not text:
            return RedactionResult(redacted_text=text, findings=(), backend="none")

        findings: list[str] = []
        backend = "local_regex"
        current = text

        if self.enable_cloud_dlp:
            dlp_result = self._redact_with_cloud_dlp(current)
            if dlp_result is not None:
                current = dlp_result.redacted_text
                findings.extend(dlp_result.findings)
                backend = dlp_result.backend

        # Always run deterministic local pass to guarantee zero leaks (e.g., API tokens).
        for info_type, pattern, replacement in _LOCAL_PII_PATTERNS:
            updated, count = pattern.subn(replacement, current)
            if count > 0:
                current = updated
                if info_type not in findings:
                    findings.append(info_type)

        return RedactionResult(
            redacted_text=current,
            findings=tuple(findings),
            backend=backend,
        )

    def redact_data(self, payload: Any) -> Any:
        """Recursively scrubs PII from dictionaries, lists, tuples, and strings."""
        if isinstance(payload, str):
            return self.redact_text(payload).redacted_text
        if isinstance(payload, dict):
            sanitized: dict[str, Any] = {}
            for key, value in payload.items():
                key_lower = str(key).lower()
                if key_lower in _SENSITIVE_FIELD_NAMES:
                    sanitized[str(key)] = "[REDACTED_FIELD]"
                else:
                    sanitized[str(key)] = self.redact_data(value)
            return sanitized
        if isinstance(payload, list):
            return [self.redact_data(item) for item in payload]
        if isinstance(payload, tuple):
            return [self.redact_data(item) for item in payload]
        return payload


_DEFAULT_REDACTOR: PiiRedactor | None = None


def get_pii_redactor() -> PiiRedactor:
    """Returns the process-wide PII redactor configured from environment variables."""
    global _DEFAULT_REDACTOR
    if _DEFAULT_REDACTOR is None:
        enable_dlp = os.environ.get("ENABLE_CLOUD_DLP", "false").lower() == "true"
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        _DEFAULT_REDACTOR = PiiRedactor(
            enable_cloud_dlp=enable_dlp,
            project_id=project_id,
        )
    return _DEFAULT_REDACTOR
