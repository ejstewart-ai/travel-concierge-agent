"""Observability, OpenTelemetry tracing, and Intent-vs-Outcome audit plugin.

Captures structured JSON records for every agent turn and tool call:
- **INTENT** (before execution): What agent or tool is about to run and with what
  PII-scrubbed parameters.
- **OUTCOME** (after execution or error): What actually happened, elapsed latency,
  whether actual outcome matched intended success, and linked OpenTelemetry span IDs.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from opentelemetry.trace import Span, Status, StatusCode

from app.observability.logging import get_structured_logger, log_structured_event
from app.observability.pii_redactor import PiiRedactor, get_pii_redactor
from app.observability.tracing import get_tracer
from app.schemas import ToolErrorResponse

_logger = get_structured_logger(__name__)


@dataclass
class _ActiveExecution:
    audit_id: str
    started_at: float
    span: Span
    intended_action: str


class ObservabilityAndAuditPlugin(BasePlugin):
    """Records OpenTelemetry spans and paired Intent/Outcome audit logs for every step."""

    def __init__(self, *, redactor: PiiRedactor | None = None) -> None:
        super().__init__(name="observability_and_audit_plugin")
        self._redactor = redactor or get_pii_redactor()
        self._tracer = get_tracer("travel_concierge.audit_plugin")
        self._active_agents: dict[str, _ActiveExecution] = {}
        self._active_tools: dict[str, _ActiveExecution] = {}
        self.audit_trail: list[dict[str, Any]] = []

    @staticmethod
    def _context_ids(ctx: Any) -> tuple[str | None, str | None]:
        inv_ctx = getattr(ctx, "_invocation_context", None)
        session = getattr(inv_ctx, "session", None) if inv_ctx is not None else None
        if session is not None:
            return getattr(session, "id", None), getattr(session, "user_id", None)
        return getattr(ctx, "session_id", None), getattr(ctx, "user_id", None)

    def _record_audit(self, entry: dict[str, Any]) -> None:
        self.audit_trail.append(entry)
        # Keep in-memory ring buffer bounded while full stream flows to JSON stdout/Cloud Logging.
        if len(self.audit_trail) > 500:
            self.audit_trail = self.audit_trail[-250:]

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> None:
        """Logs agent execution INTENT and opens an OpenTelemetry span."""
        audit_id = f"agt-{uuid.uuid4().hex[:10]}"
        session_id, user_id = self._context_ids(callback_context)
        span = self._tracer.start_span(
            name=f"agent.run.{agent.name}",
            attributes={
                "agent.name": agent.name,
                "audit.id": audit_id,
                "audit.phase": "INTENT",
                "session.id": session_id or "unknown",
            },
        )
        key = f"{id(callback_context)}:{agent.name}"
        self._active_agents[key] = _ActiveExecution(
            audit_id=audit_id,
            started_at=time.perf_counter(),
            span=span,
            intended_action=f"execute_agent:{agent.name}",
        )

        record = {
            "audit_id": audit_id,
            "phase": "INTENT",
            "agent_name": agent.name,
            "intended_action": f"execute_agent:{agent.name}",
        }
        self._record_audit(record)
        log_structured_event(
            _logger,
            logging.INFO,
            "agent_intent",
            f"Agent '{agent.name}' starting execution.",
            agent_name=agent.name,
            session_id=session_id,
            user_id=user_id,
            audit_id=audit_id,
            phase="INTENT",
            payload={"intended_action": record["intended_action"]},
        )
        return None

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> None:
        """Logs agent execution OUTCOME and closes its OpenTelemetry span."""
        session_id, user_id = self._context_ids(callback_context)
        key = f"{id(callback_context)}:{agent.name}"
        active = self._active_agents.pop(key, None)
        audit_id = active.audit_id if active else f"agt-{uuid.uuid4().hex[:10]}"
        latency_ms = (
            round((time.perf_counter() - active.started_at) * 1000.0, 2)
            if active
            else 0.0
        )

        if active is not None:
            active.span.set_attribute("audit.phase", "OUTCOME")
            active.span.set_attribute("agent.latency_ms", latency_ms)
            active.span.set_status(Status(StatusCode.OK))
            active.span.end()

        record = {
            "audit_id": audit_id,
            "phase": "OUTCOME",
            "agent_name": agent.name,
            "intended_action": active.intended_action if active else f"execute_agent:{agent.name}",
            "actual_status": "completed",
            "intent_matched": True,
            "latency_ms": latency_ms,
        }
        self._record_audit(record)
        log_structured_event(
            _logger,
            logging.INFO,
            "agent_outcome",
            f"Agent '{agent.name}' completed execution.",
            agent_name=agent.name,
            session_id=session_id,
            user_id=user_id,
            audit_id=audit_id,
            phase="OUTCOME",
            payload=record,
        )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> None:
        """Logs PII-scrubbed tool INTENT before execution and starts a child OTel span."""
        audit_id = f"tool-{uuid.uuid4().hex[:10]}"
        session_id, user_id = self._context_ids(tool_context)
        scrubbed_args = self._redactor.redact_data(tool_args)

        span = self._tracer.start_span(
            name=f"tool.execute.{tool.name}",
            attributes={
                "tool.name": tool.name,
                "audit.id": audit_id,
                "audit.phase": "INTENT",
                "session.id": session_id or "unknown",
            },
        )
        key = f"{id(tool_context)}:{tool.name}"
        self._active_tools[key] = _ActiveExecution(
            audit_id=audit_id,
            started_at=time.perf_counter(),
            span=span,
            intended_action=f"invoke_tool:{tool.name}",
        )

        record = {
            "audit_id": audit_id,
            "phase": "INTENT",
            "tool_name": tool.name,
            "intended_action": f"invoke_tool:{tool.name}",
            "intended_args": scrubbed_args,
        }
        self._record_audit(record)
        log_structured_event(
            _logger,
            logging.INFO,
            "tool_intent",
            f"Intended tool call '{tool.name}'.",
            agent_name=getattr(tool_context, "agent_name", None),
            session_id=session_id,
            user_id=user_id,
            audit_id=audit_id,
            phase="INTENT",
            payload=record,
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> None:
        """Logs paired tool OUTCOME comparing intended call vs. actual result."""
        del tool_args
        session_id, user_id = self._context_ids(tool_context)
        key = f"{id(tool_context)}:{tool.name}"
        active = self._active_tools.pop(key, None)
        audit_id = active.audit_id if active else f"tool-{uuid.uuid4().hex[:10]}"
        latency_ms = (
            round((time.perf_counter() - active.started_at) * 1000.0, 2)
            if active
            else 0.0
        )

        scrubbed_result = self._redactor.redact_data(result)
        actual_status = (
            str(result.get("status", "unknown")) if isinstance(result, dict) else "completed"
        )
        intent_matched = actual_status in ("success", "confirmed", "completed")

        if active is not None:
            active.span.set_attribute("audit.phase", "OUTCOME")
            active.span.set_attribute("tool.status", actual_status)
            active.span.set_attribute("tool.intent_matched", intent_matched)
            active.span.set_attribute("tool.latency_ms", latency_ms)
            active.span.set_status(
                Status(StatusCode.OK if intent_matched else StatusCode.ERROR)
            )
            active.span.end()

        record = {
            "audit_id": audit_id,
            "phase": "OUTCOME",
            "tool_name": tool.name,
            "intended_action": active.intended_action if active else f"invoke_tool:{tool.name}",
            "actual_status": actual_status,
            "intent_matched": intent_matched,
            "latency_ms": latency_ms,
            "outcome_summary": scrubbed_result,
        }
        self._record_audit(record)
        log_structured_event(
            _logger,
            logging.INFO if intent_matched else logging.WARNING,
            "tool_outcome",
            f"Tool '{tool.name}' finished with status '{actual_status}'.",
            agent_name=getattr(tool_context, "agent_name", None),
            session_id=session_id,
            user_id=user_id,
            audit_id=audit_id,
            phase="OUTCOME",
            payload=record,
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, Any]:
        """Captures unexpected tool exceptions as structured OUTCOME logs with LLM recovery."""
        del tool_args
        session_id, user_id = self._context_ids(tool_context)
        key = f"{id(tool_context)}:{tool.name}"
        active = self._active_tools.pop(key, None)
        audit_id = active.audit_id if active else f"tool-{uuid.uuid4().hex[:10]}"
        latency_ms = (
            round((time.perf_counter() - active.started_at) * 1000.0, 2)
            if active
            else 0.0
        )

        if active is not None:
            active.span.record_exception(error)
            active.span.set_attribute("tool.intent_matched", False)
            active.span.set_status(Status(StatusCode.ERROR, str(error)))
            active.span.end()

        record = {
            "audit_id": audit_id,
            "phase": "OUTCOME",
            "tool_name": tool.name,
            "intended_action": active.intended_action if active else f"invoke_tool:{tool.name}",
            "actual_status": "exception",
            "intent_matched": False,
            "latency_ms": latency_ms,
            "error_type": type(error).__name__,
        }
        self._record_audit(record)
        log_structured_event(
            _logger,
            logging.ERROR,
            "tool_error_outcome",
            f"Unhandled error in tool '{tool.name}'.",
            agent_name=getattr(tool_context, "agent_name", None),
            session_id=session_id,
            user_id=user_id,
            audit_id=audit_id,
            phase="OUTCOME",
            payload=record,
        )

        return ToolErrorResponse(
            error_code="UNEXPECTED_TOOL_FAILURE",
            message=f"Tool '{tool.name}' encountered an unexpected error ({type(error).__name__}).",
            recovery_steps=[
                "Apologize briefly to the traveler without exposing internal stack traces.",
                "Verify the input parameters or ask the traveler to retry with adjusted criteria.",
            ],
        ).model_dump()
