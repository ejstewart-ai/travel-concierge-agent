"""Interactive command-line runner with Human-in-the-Loop booking approval handling.

Uses ADK's `Runner`, persistent `DatabaseSessionService`, and `MemoryService`.
When the Booking Specialist triggers ADK's `adk_request_confirmation` pause, this
CLI prompts the human operator to explicitly approve or reject before resuming.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid

from google.adk.runners import Runner
from google.genai import types

from app.agent import create_app
from app.config import get_settings
from app.memory import (
    create_memory_service,
    create_session_service,
    get_memory_consolidator,
)
from app.observability.logging import get_structured_logger, log_structured_event

_logger = get_structured_logger(__name__)
_CONFIRMATION_FUNC_NAME = "adk_request_confirmation"


def _write_stdout(line: str) -> None:
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


async def run_interactive_session() -> None:
    """Runs a stateful multi-turn Travel Concierge conversation in the terminal."""
    settings = get_settings()
    adk_app = create_app(settings)
    session_service = create_session_service(settings)
    memory_service = create_memory_service(settings)

    runner = Runner(
        app=adk_app,
        session_service=session_service,
        memory_service=memory_service,
    )

    user_id = "traveler_cli"
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    await session_service.create_session(
        app_name=adk_app.name,
        user_id=user_id,
        session_id=session_id,
    )

    _write_stdout("Travel Concierge Agent ready! Type 'exit' or 'quit' to end.\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break

            message: types.Content | None = types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_input)],
            )
            invocation_id: str | None = None

            while message is not None:
                pending_confirmation_call = None
                async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=message,
                    invocation_id=invocation_id,
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if (
                                part.function_call
                                and part.function_call.name == _CONFIRMATION_FUNC_NAME
                            ):
                                pending_confirmation_call = part.function_call
                                invocation_id = event.invocation_id

                    if event.is_final_response() and event.content and event.content.parts:
                        text_chunks = [
                            p.text for p in event.content.parts if getattr(p, "text", None)
                        ]
                        if text_chunks:
                            _write_stdout(f"\nConcierge: {''.join(text_chunks)}\n")

                if pending_confirmation_call is None:
                    message = None
                    continue

                # Human-in-the-Loop approval prompt before executing booking.
                _write_stdout("\n[HUMAN APPROVAL REQUIRED] Booking details:")
                _write_stdout(f"  {pending_confirmation_call.args}")
                decision = input("Approve this booking? (yes/no): ").strip().lower()
                approved = decision in {"y", "yes"}
                log_structured_event(
                    _logger,
                    logging.INFO,
                    "hitl_decision_recorded",
                    "Recorded explicit human confirmation decision for booking.",
                    session_id=session_id,
                    user_id=user_id,
                    payload={"approved": approved},
                )
                message = types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=pending_confirmation_call.id,
                                name=_CONFIRMATION_FUNC_NAME,
                                response={"confirmed": approved},
                            )
                        )
                    ],
                )
    finally:
        await get_memory_consolidator().flush()
        _write_stdout("Session saved. Safe travels!")


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(run_interactive_session())


if __name__ == "__main__":
    main()
