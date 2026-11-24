"""Unit tests for Travel Concierge tools: schemas, happy paths, guided errors, and HITL."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.memory import TravelerMemoryStore, set_traveler_memory_store
from app.tools import (
    book_travel_reservation,
    book_travel_reservation_tool,
    lookup_destination_travel_advisory,
    needs_booking_confirmation,
    retrieve_traveler_profile,
    save_traveler_preference,
    search_flight_options,
    search_hotel_accommodations,
)


def _future_dates(days_ahead: int = 45, length: int = 4) -> tuple[str, str]:
    start = date.today() + timedelta(days=days_ahead)
    end = start + timedelta(days=length)
    return start.isoformat(), end.isoformat()


def _fake_tool_context(
    *,
    state: dict[str, Any] | None = None,
    confirmed: bool | None = None,
) -> Any:
    confirmation = (
        SimpleNamespace(confirmed=confirmed) if confirmed is not None else None
    )
    return SimpleNamespace(
        state=state if state is not None else {},
        tool_confirmation=confirmation,
    )


def test_search_flight_options_success() -> None:
    dep, ret = _future_dates()
    res = search_flight_options(
        origin_iata="sfo",
        destination_iata="hnd",
        departure_date=dep,
        return_date=ret,
        cabin_class="economy",
        max_price_usd=1200.0,
    )
    assert res["status"] == "success"
    assert res["origin_iata"] == "SFO"
    assert res["destination_iata"] == "HND"
    assert len(res["options"]) == 1
    assert res["options"][0]["flight_id"] == "FL-SFO-HND-101"


def test_search_flight_options_guided_error_on_invalid_iata() -> None:
    dep, ret = _future_dates()
    res = search_flight_options(
        origin_iata="SANFRANCISCO",
        destination_iata="HND",
        departure_date=dep,
        return_date=ret,
        cabin_class="economy",
        max_price_usd=1500.0,
    )
    assert res["status"] == "error"
    assert res["error_code"] == "INVALID_INPUT_SCHEMA"
    assert len(res["recovery_steps"]) >= 2


def test_search_flight_options_guided_error_on_past_date() -> None:
    res = search_flight_options(
        origin_iata="SFO",
        destination_iata="HND",
        departure_date="2020-01-10",
        return_date="2020-01-15",
        cabin_class="economy",
        max_price_usd=1500.0,
    )
    assert res["status"] == "error"
    assert res["error_code"] == "PAST_DEPARTURE_DATE"
    assert any("future" in step.lower() for step in res["recovery_steps"])


def test_search_flight_options_guided_error_on_tight_budget() -> None:
    dep, ret = _future_dates()
    res = search_flight_options(
        origin_iata="SFO",
        destination_iata="HND",
        departure_date=dep,
        return_date=ret,
        cabin_class="economy",
        max_price_usd=200.0,
    )
    assert res["status"] == "error"
    assert res["error_code"] == "NO_FLIGHTS_WITHIN_CONSTRAINTS"
    assert any("980" in step for step in res["recovery_steps"])


def test_search_hotel_accommodations_success_and_totals() -> None:
    dep, ret = _future_dates(length=4)
    res = search_hotel_accommodations(
        destination_city="Tokyo",
        check_in_date=dep,
        check_out_date=ret,
        max_nightly_rate_usd=300.0,
        preferred_style="boutique",
    )
    assert res["status"] == "success"
    assert res["nights"] == 4
    assert res["options"][0]["hotel_id"] == "HTL-TYO-01"
    assert res["options"][0]["total_stay_usd"] == pytest.approx(1180.0)


def test_search_hotel_accommodations_guided_error_unsupported_city() -> None:
    dep, ret = _future_dates()
    res = search_hotel_accommodations(
        destination_city="Atlantis",
        check_in_date=dep,
        check_out_date=ret,
        max_nightly_rate_usd=300.0,
        preferred_style="any",
    )
    assert res["status"] == "error"
    assert res["error_code"] == "CITY_NOT_FOUND"
    assert any("Tokyo" in step for step in res["recovery_steps"])


def test_lookup_destination_travel_advisory_success_and_fallback() -> None:
    ok = lookup_destination_travel_advisory("Tokyo", "October")
    assert ok["status"] == "success"
    assert ok["country"] == "Japan"
    assert "October:" in ok["seasonal_weather"]

    missing = lookup_destination_travel_advisory("Narnia", "October")
    assert missing["status"] == "error"
    assert missing["error_code"] == "ADVISORY_UNAVAILABLE"


def test_save_and_retrieve_traveler_profile_with_pii_redaction(tmp_path: Any) -> None:
    store = TravelerMemoryStore(tmp_path / "memory.db")
    set_traveler_memory_store(store)
    try:
        ctx = _fake_tool_context()
        saved = save_traveler_preference(
            traveler_id="traveler_42",
            category="hotel_style",
            preference_value="Boutique hotels near parks; reach me at jane@example.com",
            tool_context=ctx,
        )
        assert saved["status"] == "success"
        assert saved["pii_was_redacted"] is True
        assert "jane@example.com" not in saved["stored_value"]
        assert "[REDACTED_EMAIL]" in saved["stored_value"]

        profile = retrieve_traveler_profile(
            traveler_id="traveler_42",
            query="Tokyo boutique hotel",
            tool_context=ctx,
        )
        assert profile["status"] == "success"
        assert "hotel_style" in profile["preferences"]
        assert "[REDACTED_EMAIL]" in profile["preferences"]["hotel_style"]
    finally:
        set_traveler_memory_store(None)


def test_booking_tool_requires_human_confirmation_and_handles_approval() -> None:
    # Verify ADK HITL confirmation gate is explicitly attached on the FunctionTool.
    assert book_travel_reservation_tool._require_confirmation is needs_booking_confirmation
    assert (
        needs_booking_confirmation(
            item_type="flight",
            item_id="FL-SFO-HND-101",
            traveler_name="Alex Rivera",
            total_cost_usd=980.0,
            trip_dates="2026-10-10 to 2026-10-14",
        )
        is True
    )

    # Declined confirmation must NOT confirm booking.
    declined_ctx = _fake_tool_context(confirmed=False)
    declined = book_travel_reservation(
        item_type="flight",
        item_id="FL-SFO-HND-101",
        traveler_name="Alex Rivera",
        total_cost_usd=980.0,
        trip_dates="2026-10-10 to 2026-10-14",
        tool_context=declined_ctx,
    )
    assert declined["status"] == "rejected"
    assert declined["confirmation_code"] is None

    # Approved confirmation issues deterministic format confirmation code and writes state.
    approved_ctx = _fake_tool_context(confirmed=True)
    approved = book_travel_reservation(
        item_type="flight",
        item_id="FL-SFO-HND-101",
        traveler_name="Alex Rivera",
        total_cost_usd=980.0,
        trip_dates="2026-10-10 to 2026-10-14",
        tool_context=approved_ctx,
    )
    assert approved["status"] == "confirmed"
    assert approved["confirmation_code"].startswith("TC-FLI-")
    assert (
        approved_ctx.state["last_booking_confirmation"]["confirmation_code"]
        == approved["confirmation_code"]
    )
