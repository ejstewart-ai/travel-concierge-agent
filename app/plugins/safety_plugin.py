"""Guardrails and policy enforcement plugin for the Travel Concierge ADK App.

Implements defense-in-depth safety controls via ADK `BasePlugin` hooks:
- `before_model_callback`: Detects prompt-injection / approval-bypass attempts
  and policy violations before the prompt reaches Gemini, with an optional
  Google Cloud Model Armor hook for enterprise deployments.
- `after_model_callback`: Scrubs any leaked PII from model outputs and verifies
  that the assistant never fabricates a booking confirmation code without a
  human-approved tool result in session state.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from app.observability.logging import get_structured_logger, log_structured_event
from app.observability.pii_redactor import PiiRedactor, get_pii_redactor

_logger = get_structured_logger(__name__)

_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"bypass\s+(human\s+)?(approval|confirmation|guardrail|policy)", re.I),
    re.compile(r"skip\s+(human\s+)?(approval|confirmation)", re.I),
    re.compile(r"(reveal|dump|print)\s+(your\s+)?(system\s+prompt|constitution)", re.I),
    re.compile(r"\b(jailbreak|developer\s+mode|override\s+safety)\b", re.I),
)

_UNSAFE_TRAVEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(fake|forged|counterfeit)\s+(passport|visa|id)\b", re.I),
    re.compile(r"\b(smuggl(e|ing)|bypass\s+customs|evade\s+border)\b", re.I),
    re.compile(r"\bstolen\s+credit\s+card\b", re.I),
)

_CONFIRMATION_CODE_RE = re.compile(r"\bTC-(?:FLI|HOT)-[A-F0-9]{6}\b")


def _extract_latest_user_text(llm_request: LlmRequest) -> str:
    """Extracts the text from the most recent user message in an LlmRequest."""
    contents = getattr(llm_request, "contents", None) or []
    for content in reversed(contents):
        if getattr(content, "role", None) == "user":
            parts = getattr(content, "parts", None) or []
            texts = [part.text for part in parts if getattr(part, "text", None)]
            if texts:
                return "\n".join(texts)
    return ""


class TravelSafetyAndPolicyPlugin(BasePlugin):
    """Enforces input/output guardrails across all agents in the ADK App."""

    def __init__(
        self,
        *,
        redactor: PiiRedactor | None = None,
        model_armor_template: str | None = None,
    ) -> None:
        super().__init__(name="travel_safety_and_policy_plugin")
        self._redactor = redactor or get_pii_redactor()
        self.model_armor_template = model_armor_template

    def _check_input_policy(self, text: str) -> str | None:
        """Returns a policy violation reason if the user prompt must be blocked."""
        for pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(text):
                return "PROMPT_INJECTION_OR_APPROVAL_BYPASS"
        for pattern in _UNSAFE_TRAVEL_PATTERNS:
            if pattern.search(text):
                return "UNSAFE_OR_ILLEGAL_TRAVEL_REQUEST"
        if self.model_armor_template:
            armor_block = self._check_model_armor(text)
            if armor_block:
                return armor_block
        return None

    def _check_model_armor(self, text: str) -> str | None:
        """Optional Google Cloud Model Armor screening hook when configured."""
        if not self.model_armor_template or not text:
            return None
        try:
            from google.cloud import modelarmor_v1  # type: ignore[import-untyped]

            client = modelarmor_v1.ModelArmorClient()
            response = client.sanitize_user_prompt(
                request={
                    "name": self.model_armor_template,
                    "user_prompt_data": {"text": text},
                }
            )
            match_state = str(
                getattr(response.sanitization_result, "filter_match_state", "")
            )
            if "MATCH_FOUND" in match_state:
                return "GOOGLE_CLOUD_MODEL_ARMOR_BLOCK"
        except Exception:
            # Fail open on SDK absence locally while retaining local policy rules above.
            return None
        return None

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Blocks prompt-injection attempts and unsafe requests before LLM inference."""
        user_text = _extract_latest_user_text(llm_request)
        if not user_text:
            return None

        violation = self._check_input_policy(user_text)
        if violation is None:
            return None

        agent_name = getattr(callback_context, "agent_name", "unknown_agent")
        log_structured_event(
            _logger,
            logging.WARNING,
            "guardrail_input_blocked",
            "Safety guardrail blocked an incoming request before model execution.",
            agent_name=agent_name,
            payload={"violation_code": violation},
        )

        refusal = (
            "I cannot process that request because it violates my travel safety and "
            "approval policies. I am happy to help you search legitimate flights, hotels, "
            "or destination advisories, and all bookings require your explicit approval."
        )
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=refusal)],
            )
        )

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Scrubs leaked PII and blocks unverified booking confirmations in outputs."""
        content = getattr(llm_response, "content", None)
        if content is None or not getattr(content, "parts", None):
            return None

        modified = False
        new_parts: list[Any] = []
        state = getattr(callback_context, "state", {}) or {}
        verified_booking = state.get("last_booking_confirmation") or {}
        verified_code = (
            verified_booking.get("confirmation_code")
            if isinstance(verified_booking, dict)
            else None
        )

        for part in content.parts:
            text = getattr(part, "text", None)
            if not text:
                new_parts.append(part)
                continue

            redaction = self._redactor.redact_text(text)
            cleaned_text = redaction.redacted_text
            if redaction.had_pii:
                modified = True
                log_structured_event(
                    _logger,
                    logging.WARNING,
                    "guardrail_output_pii_redacted",
                    "Redacted sensitive PII detected in model response.",
                    agent_name=getattr(callback_context, "agent_name", None),
                    payload={"redacted_types": list(redaction.findings)},
                )

            # Prevent hallucinated booking confirmation codes not backed by HITL execution.
            for code in _CONFIRMATION_CODE_RE.findall(cleaned_text):
                if code != verified_code:
                    cleaned_text = cleaned_text.replace(
                        code, "[UNVERIFIED_BOOKING_CODE_REMOVED]"
                    )
                    modified = True
                    log_structured_event(
                        _logger,
                        logging.WARNING,
                        "guardrail_unverified_booking_blocked",
                        "Removed unverified booking confirmation code from model output.",
                        agent_name=getattr(callback_context, "agent_name", None),
                    )

            new_parts.append(types.Part.from_text(text=cleaned_text))

        if not modified:
            return None

        return LlmResponse(
            content=types.Content(
                role=getattr(content, "role", "model") or "model",
                parts=new_parts,
            )
        )
