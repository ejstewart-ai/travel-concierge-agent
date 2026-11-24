"""Unit tests for persistent memory store, async background consolidation, and sessions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.sessions import DatabaseSessionService

from app.config import AppSettings
from app.memory import (
    AsyncMemoryConsolidator,
    TravelerMemoryStore,
    async_memory_after_agent_callback,
    create_session_service,
    get_memory_consolidator,
    set_traveler_memory_store,
)


def test_traveler_memory_store_persists_across_restarts(tmp_path: Path) -> None:
    db_file = tmp_path / "traveler.db"
    store_first = TravelerMemoryStore(db_file)
    store_first.save_preference("u1", "dietary", "Vegetarian; phone 415-555-0199")
    store_first.add_consolidated_memory(
        "u1", "s1", "Loved Tokyo Shibuya boutique hotels and ramen shops."
    )
    store_first.add_consolidated_memory(
        "u1", "s2", "Enjoyed Lisbon historic trams and coastal food tours."
    )

    # Re-open a fresh instance pointing at the same SQLite database to verify durability.
    store_reloaded = TravelerMemoryStore(db_file)
    prefs = store_reloaded.get_preferences("u1")
    assert prefs["dietary"] == "Vegetarian; phone [REDACTED_PHONE]"

    matches = store_reloaded.search_memories("u1", "Tokyo boutique hotel", limit=2)
    assert len(matches) >= 1
    assert "Tokyo" in matches[0]


@pytest.mark.asyncio
async def test_async_memory_consolidation_runs_in_background_and_redacts_pii(
    tmp_path: Path,
) -> None:
    store = TravelerMemoryStore(tmp_path / "async_mem.db")
    consolidator = AsyncMemoryConsolidator(store)

    task = consolidator.schedule_consolidation(
        traveler_id="u_async",
        session_id="sess_99",
        summary_text="Planned Paris trip; contact me at traveler@example.com or 123-45-6789.",
    )
    assert not task.cancelled()
    await consolidator.flush()

    memories = store.search_memories("u_async", "Paris trip")
    assert len(memories) == 1
    assert "traveler@example.com" not in memories[0]
    assert "123-45-6789" not in memories[0]
    assert "[REDACTED_EMAIL]" in memories[0]
    assert "[REDACTED_SSN]" in memories[0]


@pytest.mark.asyncio
async def test_after_agent_callback_schedules_non_blocking_memory(
    tmp_path: Path,
) -> None:
    store = TravelerMemoryStore(tmp_path / "cb_mem.db")
    set_traveler_memory_store(store)
    try:
        fake_ctx = SimpleNamespace(
            state={"draft_itinerary": "4 days in Tokyo staying at Trunk Hotel Yoyogi Park."},
            _invocation_context=SimpleNamespace(
                session=SimpleNamespace(id="sess_cb", user_id="u_cb")
            ),
        )
        result = await async_memory_after_agent_callback(fake_ctx)  # type: ignore[arg-type]
        assert result is None
        await get_memory_consolidator().flush()

        memories = store.search_memories("u_cb", "Tokyo Trunk Hotel")
        assert len(memories) == 1
        assert "Trunk Hotel" in memories[0]
    finally:
        set_traveler_memory_store(None)


def test_create_session_service_uses_persistent_database(tmp_path: Path) -> None:
    settings = AppSettings(
        session_database_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}"
    )
    service = create_session_service(settings)
    assert isinstance(service, DatabaseSessionService)
