"""Travel Concierge ADK tools with strict Pydantic schemas, guided error recovery, and HITL hooks.

All tool functions follow AgentOps best practices:
- Highly specific verb-noun naming (`search_flight_options`, `book_travel_reservation`, etc.)
- Comprehensive Google-style docstrings explaining purpose, parameters, and return structure
- Strict Pydantic v2 input/output validation (`app/schemas.py`)
- Actionable recovery instructions returned to the LLM on invalid input or missing data
- Native ADK Human-in-the-Loop confirmation (`require_confirmation`) for high-stakes bookings
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from google.adk.tools import FunctionTool, ToolContext
from pydantic import ValidationError

from app.config import get_settings
from app.memory import get_traveler_memory_store
from app.schemas import (
    BookingConfirmation,
    BookingRequest,
    DestinationAdvisoryRequest,
    DestinationAdvisoryResponse,
    FlightOption,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelOption,
    HotelSearchRequest,
    HotelSearchResponse,
    RetrieveProfileRequest,
    RetrieveProfileResponse,
    SavePreferenceRequest,
    SavePreferenceResponse,
    ToolErrorResponse,
)

# Curated deterministic catalog so the agent works reliably out-of-the-box for demos,
# local development, and reproducible ADK evaluations with zero external API keys needed.
_FLIGHT_CATALOG: list[dict[str, Any]] = [
    {
        "flight_id": "FL-SFO-HND-101",
        "airline": "ANA All Nippon Airways",
        "origin_iata": "SFO",
        "destination_iata": "HND",
        "cabin_class": "economy",
        "nonstop": True,
        "duration_hours": 11.2,
        "price_usd": 980.0,
    },
    {
        "flight_id": "FL-SFO-HND-204",
        "airline": "Japan Airlines",
        "origin_iata": "SFO",
        "destination_iata": "HND",
        "cabin_class": "premium_economy",
        "nonstop": True,
        "duration_hours": 11.4,
        "price_usd": 1490.0,
    },
    {
        "flight_id": "FL-SFO-HND-309",
        "airline": "United Airlines",
        "origin_iata": "SFO",
        "destination_iata": "HND",
        "cabin_class": "business",
        "nonstop": True,
        "duration_hours": 11.1,
        "price_usd": 3850.0,
    },
    {
        "flight_id": "FL-JFK-CDG-112",
        "airline": "Air France",
        "origin_iata": "JFK",
        "destination_iata": "CDG",
        "cabin_class": "economy",
        "nonstop": True,
        "duration_hours": 7.5,
        "price_usd": 740.0,
    },
    {
        "flight_id": "FL-JFK-LIS-205",
        "airline": "TAP Air Portugal",
        "origin_iata": "JFK",
        "destination_iata": "LIS",
        "cabin_class": "economy",
        "nonstop": True,
        "duration_hours": 6.9,
        "price_usd": 620.0,
    },
    {
        "flight_id": "FL-LAX-LHR-401",
        "airline": "Virgin Atlantic",
        "origin_iata": "LAX",
        "destination_iata": "LHR",
        "cabin_class": "economy",
        "nonstop": True,
        "duration_hours": 10.5,
        "price_usd": 810.0,
    },
]

_HOTEL_CATALOG: list[dict[str, Any]] = [
    {
        "hotel_id": "HTL-TYO-01",
        "name": "Trunk Hotel Yoyogi Park",
        "city": "Tokyo",
        "neighborhood": "Shibuya / Yoyogi",
        "style": "boutique",
        "rating": 4.8,
        "nightly_rate_usd": 295.0,
        "highlights": ["Rooftop infinity pool", "Walkable to Yoyogi Park", "Local coffee bar"],
    },
    {
        "hotel_id": "HTL-TYO-02",
        "name": "Shibuya Stream Hotel",
        "city": "Tokyo",
        "neighborhood": "Shibuya",
        "style": "business",
        "rating": 4.5,
        "nightly_rate_usd": 210.0,
        "highlights": ["Direct subway access", "Fast Wi-Fi", "Modern minimalist rooms"],
    },
    {
        "hotel_id": "HTL-TYO-03",
        "name": "Hotel Gracery Shinjuku",
        "city": "Tokyo",
        "neighborhood": "Shinjuku",
        "style": "budget",
        "rating": 4.3,
        "nightly_rate_usd": 145.0,
        "highlights": ["Central transit hub", "Concierge desk", "Iconic Godzilla terrace"],
    },
    {
        "hotel_id": "HTL-PAR-01",
        "name": "Hotel Le Marais Courtyard",
        "city": "Paris",
        "neighborhood": "Le Marais",
        "style": "boutique",
        "rating": 4.7,
        "nightly_rate_usd": 275.0,
        "highlights": ["Historic courtyard", "Artisan bakery breakfast", "Walk to Louvre"],
    },
    {
        "hotel_id": "HTL-LIS-01",
        "name": "AlmaLusa Baixa Chiado",
        "city": "Lisbon",
        "neighborhood": "Baixa",
        "style": "boutique",
        "rating": 4.8,
        "nightly_rate_usd": 220.0,
        "highlights": ["18th-century building", "Walkable to riverfront", "Quiet square"],
    },
    {
        "hotel_id": "HTL-LON-01",
        "name": "The Hoxton Holborn",
        "city": "London",
        "neighborhood": "Holborn",
        "style": "boutique",
        "rating": 4.6,
        "nightly_rate_usd": 260.0,
        "highlights": ["Near Covent Garden", "Lively lobby workspace", "Complimentary bikes"],
    },
]

_ADVISORY_CATALOG: dict[str, dict[str, Any]] = {
    "tokyo": {
        "destination_city": "Tokyo",
        "country": "Japan",
        "safety_level": "Level 1 - Exercise Normal Precautions",
        "visa_guidance": (
            "Visa-free short stays up to 90 days for many passports; "
            "verify passport validity before travel."
        ),
        "seasonal_weather": (
            "Mild, crisp days (15-22C) in spring/autumn; pack layers and an umbrella."
        ),
        "local_tips": [
            "Load a digital Suica or PASMO card in your phone wallet for seamless subway transit.",
            "Carry some Japanese yen cash for small ramen shops and temple donations.",
            "Reserve popular restaurants and Shinkansen oversized-luggage seats in advance.",
        ],
    },
    "paris": {
        "destination_city": "Paris",
        "country": "France",
        "safety_level": "Level 2 - Exercise Increased Caution",
        "visa_guidance": (
            "Schengen short-stay rules apply; confirm ETIAS and entry rules ahead of departure."
        ),
        "seasonal_weather": (
            "Cool mornings and pleasant afternoons; bring walking shoes and a light coat."
        ),
        "local_tips": [
            "Book timed-entry tickets for the Louvre and Musee d'Orsay weeks ahead.",
            "Keep belongings zipped on busy Metro lines and around major landmarks.",
            "Use Navigo Easy passes for effortless Metro and RER rides.",
        ],
    },
    "lisbon": {
        "destination_city": "Lisbon",
        "country": "Portugal",
        "safety_level": "Level 1 - Exercise Normal Precautions",
        "visa_guidance": (
            "Schengen Area entry rules apply; passport must remain valid 3+ months past departure."
        ),
        "seasonal_weather": (
            "Sunny coastal climate with breezy evenings; sturdy shoes help on cobblestone hills."
        ),
        "local_tips": [
            "Ride Tram 28 early in the morning to avoid long queues.",
            "Purchase a Navegante card for trams, buses, and the Sintra day-trip train.",
            "Try Pasteis de Belem and book sunset viewpoints in Alfama.",
        ],
    },
    "london": {
        "destination_city": "London",
        "country": "United Kingdom",
        "safety_level": "Level 2 - Exercise Increased Caution",
        "visa_guidance": (
            "Verify UK Electronic Travel Authorisation (ETA) eligibility before flying."
        ),
        "seasonal_weather": (
            "Changeable skies with intermittent showers; carry an umbrella and waterproof layer."
        ),
        "local_tips": [
            "Tap a contactless card or phone at Tube and bus gates (daily fare cap applies).",
            "Major national museums like the British Museum and Tate Modern have free entry.",
            "Book West End theatre and Heathrow Express tickets early for lower fares.",
        ],
    },
}


def _validation_error_payload(exc: ValidationError, tool_name: str) -> dict[str, Any]:
    """Converts a Pydantic ValidationError into a guided recovery dict for the LLM."""
    details = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
        for err in exc.errors()
    )
    return ToolErrorResponse(
        error_code="INVALID_INPUT_SCHEMA",
        message=f"Input validation failed for {tool_name}: {details}",
        recovery_steps=[
            "Review the parameter format requirements in the tool description.",
            "Ask the traveler to clarify or correct the invalid field(s) before retrying.",
            "Use 3-letter uppercase IATA codes (e.g., SFO, HND) and YYYY-MM-DD dates.",
        ],
    ).model_dump()


def search_flight_options(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    return_date: str,
    cabin_class: str,
    max_price_usd: float,
) -> dict[str, Any]:
    """Searches available round-trip flights matching route, dates, cabin class, and budget.

    Args:
        origin_iata: Three-letter uppercase IATA airport code for departure (e.g., "SFO", "JFK").
        destination_iata: Three-letter uppercase IATA airport code for arrival (e.g., "HND", "CDG").
        departure_date: Outbound travel date in ISO YYYY-MM-DD format.
        return_date: Return travel date in ISO YYYY-MM-DD format (must be after departure_date).
        cabin_class: Desired cabin tier; must be one of "economy", "premium_economy", or "business".
        max_price_usd: Maximum acceptable round-trip price in US dollars (positive number).

    Returns:
        A JSON-serializable dictionary conforming to `FlightSearchResponse` with matching
        flight options, or `ToolErrorResponse` containing actionable `recovery_steps` if
        inputs are invalid, dates are in the past, or no flights fit the budget.
    """
    try:
        req = FlightSearchRequest(
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            departure_date=date.fromisoformat(departure_date),
            return_date=date.fromisoformat(return_date),
            cabin_class=cabin_class.strip().lower(),  # type: ignore[arg-type]
            max_price_usd=max_price_usd,
        )
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            return _validation_error_payload(exc, "search_flight_options")
        return ToolErrorResponse(
            error_code="INVALID_DATE_FORMAT",
            message=f"Could not parse flight dates: {exc}",
            recovery_steps=[
                "Convert departure_date and return_date to strict YYYY-MM-DD format.",
                "Ensure return_date occurs after departure_date and retry search_flight_options.",
            ],
        ).model_dump()

    if req.departure_date < date.today():
        return ToolErrorResponse(
            error_code="PAST_DEPARTURE_DATE",
            message=f"Departure date {req.departure_date.isoformat()} is in the past.",
            recovery_steps=[
                "Inform the traveler that flights cannot be searched for past dates.",
                "Ask the traveler for an upcoming future departure and return date.",
            ],
        ).model_dump()

    route_matches = [
        item
        for item in _FLIGHT_CATALOG
        if item["origin_iata"] == req.origin_iata
        and item["destination_iata"] == req.destination_iata
    ]
    if not route_matches:
        supported = sorted(
            {f"{f['origin_iata']}->{f['destination_iata']}" for f in _FLIGHT_CATALOG}
        )
        return ToolErrorResponse(
            error_code="ROUTE_NOT_FOUND",
            message=(
                f"No direct catalog flights found for {req.origin_iata} -> {req.destination_iata}."
            ),
            recovery_steps=[
                f"Offer one of the currently supported routes: {', '.join(supported)}.",
                (
                    "Ask the traveler if they would like to depart from a nearby major hub "
                    "(e.g., SFO, JFK, LAX)."
                ),
            ],
        ).model_dump()

    filtered = [
        FlightOption(
            flight_id=item["flight_id"],
            airline=item["airline"],
            origin_iata=item["origin_iata"],
            destination_iata=item["destination_iata"],
            departure_date=req.departure_date.isoformat(),
            return_date=req.return_date.isoformat(),
            cabin_class=item["cabin_class"],
            nonstop=item["nonstop"],
            duration_hours=item["duration_hours"],
            price_usd=item["price_usd"],
        )
        for item in route_matches
        if item["cabin_class"] == req.cabin_class and item["price_usd"] <= req.max_price_usd
    ]

    if not filtered:
        lowest = min(item["price_usd"] for item in route_matches)
        cabins = sorted({item["cabin_class"] for item in route_matches})
        return ToolErrorResponse(
            error_code="NO_FLIGHTS_WITHIN_CONSTRAINTS",
            message=(
                f"No {req.cabin_class} flights found for {req.origin_iata}->{req.destination_iata} "
                f"at or below ${req.max_price_usd:.0f}."
            ),
            recovery_steps=[
                f"Suggest raising max_price_usd to at least ${lowest:.0f}.",
                (
                    "Or retry with one of the available cabin classes on this route: "
                    f"{', '.join(cabins)}."
                ),
            ],
        ).model_dump()

    return FlightSearchResponse(
        origin_iata=req.origin_iata,
        destination_iata=req.destination_iata,
        options=filtered,
    ).model_dump()


def search_hotel_accommodations(
    destination_city: str,
    check_in_date: str,
    check_out_date: str,
    max_nightly_rate_usd: float,
    preferred_style: str,
) -> dict[str, Any]:
    """Searches curated hotels by city, stay dates, nightly budget, and accommodation style.

    Args:
        destination_city: Target city name (e.g., "Tokyo", "Paris", "Lisbon", "London").
        check_in_date: Hotel check-in date in ISO YYYY-MM-DD format.
        check_out_date: Hotel check-out date in ISO YYYY-MM-DD format (after check_in_date).
        max_nightly_rate_usd: Maximum acceptable room rate per night in USD.
        preferred_style: Style preference; one of "boutique", "luxury", "budget",
            "business", or "any".

    Returns:
        A JSON-serializable dictionary conforming to `HotelSearchResponse` including
        total stay pricing, or `ToolErrorResponse` with concrete `recovery_steps`.
    """
    try:
        req = HotelSearchRequest(
            destination_city=destination_city.strip(),
            check_in_date=date.fromisoformat(check_in_date),
            check_out_date=date.fromisoformat(check_out_date),
            max_nightly_rate_usd=max_nightly_rate_usd,
            preferred_style=preferred_style.strip().lower(),  # type: ignore[arg-type]
        )
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            return _validation_error_payload(exc, "search_hotel_accommodations")
        return ToolErrorResponse(
            error_code="INVALID_DATE_FORMAT",
            message=f"Could not parse hotel stay dates: {exc}",
            recovery_steps=[
                "Provide check_in_date and check_out_date in YYYY-MM-DD format.",
                "Ensure check_out_date is later than check_in_date and retry.",
            ],
        ).model_dump()

    nights = (req.check_out_date - req.check_in_date).days
    city_matches = [
        h for h in _HOTEL_CATALOG if h["city"].lower() == req.destination_city.lower()
    ]
    if not city_matches:
        supported_cities = sorted({h["city"] for h in _HOTEL_CATALOG})
        return ToolErrorResponse(
            error_code="CITY_NOT_FOUND",
            message=f"No partner hotels currently indexed for '{req.destination_city}'.",
            recovery_steps=[
                f"Suggest one of our supported cities: {', '.join(supported_cities)}.",
                "Ask the traveler if they would like to explore one of these destinations instead.",
            ],
        ).model_dump()

    matched = [
        HotelOption(
            hotel_id=h["hotel_id"],
            name=h["name"],
            city=h["city"],
            neighborhood=h["neighborhood"],
            style=h["style"],
            rating=h["rating"],
            nightly_rate_usd=h["nightly_rate_usd"],
            total_stay_usd=round(h["nightly_rate_usd"] * nights, 2),
            highlights=h["highlights"],
        )
        for h in city_matches
        if h["nightly_rate_usd"] <= req.max_nightly_rate_usd
        and (req.preferred_style == "any" or h["style"] == req.preferred_style)
    ]

    if not matched:
        lowest_rate = min(h["nightly_rate_usd"] for h in city_matches)
        styles = sorted({h["style"] for h in city_matches})
        return ToolErrorResponse(
            error_code="NO_HOTELS_WITHIN_CONSTRAINTS",
            message=(
                f"No {req.preferred_style} hotels in {req.destination_city} found under "
                f"${req.max_nightly_rate_usd:.0f}/night."
            ),
            recovery_steps=[
                f"Retry with preferred_style='any' or one of: {', '.join(styles)}.",
                f"Or adjust max_nightly_rate_usd to at least ${lowest_rate:.0f}/night.",
            ],
        ).model_dump()

    return HotelSearchResponse(
        destination_city=city_matches[0]["city"],
        nights=nights,
        options=matched,
    ).model_dump()


def lookup_destination_travel_advisory(
    destination_city: str,
    travel_month: str,
) -> dict[str, Any]:
    """Retrieves safety level, visa guidance, seasonal weather, and local tips for a city.

    Args:
        destination_city: City to check advisories for (e.g., "Tokyo", "Paris", "Lisbon", "London").
        travel_month: Planned month of travel (e.g., "October").

    Returns:
        A JSON-serializable dictionary conforming to `DestinationAdvisoryResponse`,
        or `ToolErrorResponse` with recovery guidance if the destination is unsupported.
    """
    try:
        req = DestinationAdvisoryRequest(
            destination_city=destination_city.strip(),
            travel_month=travel_month.strip(),
        )
    except ValidationError as exc:
        return _validation_error_payload(exc, "lookup_destination_travel_advisory")

    advisory = _ADVISORY_CATALOG.get(req.destination_city.lower())
    if advisory is None:
        supported = ", ".join(sorted(v["destination_city"] for v in _ADVISORY_CATALOG.values()))
        return ToolErrorResponse(
            error_code="ADVISORY_UNAVAILABLE",
            message=f"Travel advisory data is not yet available for '{req.destination_city}'.",
            recovery_steps=[
                f"Let the traveler know curated advisories exist for: {supported}.",
                (
                    "Advise checking official government travel portals before booking "
                    "international trips."
                ),
            ],
        ).model_dump()

    return DestinationAdvisoryResponse(
        destination_city=advisory["destination_city"],
        country=advisory["country"],
        safety_level=advisory["safety_level"],
        visa_guidance=advisory["visa_guidance"],
        seasonal_weather=f"{req.travel_month}: {advisory['seasonal_weather']}",
        local_tips=advisory["local_tips"],
    ).model_dump()


def save_traveler_preference(
    traveler_id: str,
    category: str,
    preference_value: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Persists a traveler preference into the database after active PII redaction.

    Args:
        traveler_id: Unique non-PII identifier for the traveler (e.g., "traveler_demo").
        category: Preference category; one of "hotel_style", "airline_seating", "dietary",
            "budget_tier", "pace", "interests", or "general".
        preference_value: The preference description to store (any accidental PII is auto-redacted).

    Returns:
        A JSON-serializable dictionary conforming to `SavePreferenceResponse` or
        `ToolErrorResponse` if validation fails.
    """
    try:
        req = SavePreferenceRequest(
            traveler_id=traveler_id.strip(),
            category=category.strip().lower(),  # type: ignore[arg-type]
            preference_value=preference_value.strip(),
        )
    except ValidationError as exc:
        return _validation_error_payload(exc, "save_traveler_preference")

    store = get_traveler_memory_store()
    stored_value, pii_redacted = store.save_preference(
        req.traveler_id, req.category, req.preference_value
    )
    # Mirror in ADK user-scoped session state so sub-agents see updates immediately.
    tool_context.state[f"user:pref_{req.category}"] = stored_value

    return SavePreferenceResponse(
        traveler_id=req.traveler_id,
        category=req.category,
        stored_value=stored_value,
        pii_was_redacted=pii_redacted,
    ).model_dump()


def retrieve_traveler_profile(
    traveler_id: str,
    query: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Loads persisted traveler preferences and relevant long-term trip memories.

    Args:
        traveler_id: Unique non-PII identifier for the traveler (e.g., "traveler_demo").
        query: Current trip topic or destination used to rank relevant past memories.

    Returns:
        A JSON-serializable dictionary conforming to `RetrieveProfileResponse` containing
        stored preferences and top relevant memories, or `ToolErrorResponse` on invalid input.
    """
    try:
        req = RetrieveProfileRequest(
            traveler_id=traveler_id.strip(),
            query=query.strip(),
        )
    except ValidationError as exc:
        return _validation_error_payload(exc, "retrieve_traveler_profile")

    store = get_traveler_memory_store()
    preferences = store.get_preferences(req.traveler_id)
    memories = store.search_memories(req.traveler_id, req.query, limit=5)

    tool_context.state["user:loaded_preferences"] = preferences

    return RetrieveProfileResponse(
        traveler_id=req.traveler_id,
        preferences=preferences,
        relevant_memories=memories,
    ).model_dump()


def needs_booking_confirmation(
    item_type: str,
    item_id: str,
    traveler_name: str,
    total_cost_usd: float,
    trip_dates: str,
    **kwargs: Any,
) -> bool:
    """Human-in-the-Loop policy gate determining whether a booking requires human approval.

    By default (`hitl_confirmation_threshold_usd == 0.0`), EVERY flight or hotel
    booking pauses for explicit human confirmation before execution.
    """
    del item_type, item_id, traveler_name, trip_dates, kwargs
    threshold = get_settings().hitl_confirmation_threshold_usd
    return float(total_cost_usd) >= threshold


def book_travel_reservation(
    item_type: str,
    item_id: str,
    traveler_name: str,
    total_cost_usd: float,
    trip_dates: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Executes a high-stakes flight or hotel booking after explicit human confirmation.

    This tool is wrapped with ADK's native `FunctionTool(require_confirmation=...)`
    Human-in-the-Loop hook so execution automatically pauses until the traveler
    explicitly approves the cost and reservation details.

    Args:
        item_type: Reservation category; must be either "flight" or "hotel".
        item_id: Verified catalog identifier returned by search tools (e.g., "FL-SFO-HND-101").
        traveler_name: Full name of the traveler for the reservation voucher.
        total_cost_usd: Total confirmed price in US dollars.
        trip_dates: Date window for the reservation (e.g., "2026-10-10 to 2026-10-14").

    Returns:
        A JSON-serializable dictionary conforming to `BookingConfirmation` once approved,
        or `ToolErrorResponse` with recovery instructions if inputs or item IDs are invalid.
    """
    try:
        req = BookingRequest(
            item_type=item_type.strip().lower(),  # type: ignore[arg-type]
            item_id=item_id.strip().upper(),
            traveler_name=traveler_name.strip(),
            total_cost_usd=total_cost_usd,
            trip_dates=trip_dates.strip(),
        )
    except ValidationError as exc:
        return _validation_error_payload(exc, "book_travel_reservation")

    valid_ids = {f["flight_id"] for f in _FLIGHT_CATALOG} | {
        h["hotel_id"] for h in _HOTEL_CATALOG
    }
    if req.item_id not in valid_ids:
        return ToolErrorResponse(
            error_code="UNKNOWN_CATALOG_ITEM",
            message=f"Reservation item '{req.item_id}' was not found in the verified catalog.",
            recovery_steps=[
                (
                    "Run search_flight_options or search_hotel_accommodations first "
                    "to obtain a valid item_id."
                ),
                "Present verified options to the traveler before requesting booking approval.",
            ],
        ).model_dump()

    # Defense-in-depth check on ADK ToolConfirmation if invoked programmatically.
    confirmation = getattr(tool_context, "tool_confirmation", None)
    if confirmation is not None and not getattr(confirmation, "confirmed", False):
        return BookingConfirmation(
            status="rejected",
            confirmation_code=None,
            item_type=req.item_type,
            item_id=req.item_id,
            total_cost_usd=req.total_cost_usd,
            message="Booking cancelled because explicit human approval was declined.",
        ).model_dump()

    confirmation_code = f"TC-{req.item_type[:3].upper()}-{uuid.uuid4().hex[:6].upper()}"
    result = BookingConfirmation(
        status="confirmed",
        confirmation_code=confirmation_code,
        item_type=req.item_type,
        item_id=req.item_id,
        total_cost_usd=req.total_cost_usd,
        message=(
            f"Human-approved {req.item_type} reservation {req.item_id} confirmed for "
            f"{req.trip_dates} (${req.total_cost_usd:.2f})."
        ),
    ).model_dump()

    tool_context.state["last_booking_confirmation"] = {
        "confirmation_code": confirmation_code,
        "item_id": req.item_id,
        "item_type": req.item_type,
        "total_cost_usd": req.total_cost_usd,
    }
    return result


# Explicit FunctionTool wrappers so ADK enforces Human-in-the-Loop confirmation on bookings.
search_flight_options_tool = FunctionTool(func=search_flight_options)
search_hotel_accommodations_tool = FunctionTool(func=search_hotel_accommodations)
lookup_destination_travel_advisory_tool = FunctionTool(
    func=lookup_destination_travel_advisory
)
save_traveler_preference_tool = FunctionTool(func=save_traveler_preference)
retrieve_traveler_profile_tool = FunctionTool(func=retrieve_traveler_profile)
book_travel_reservation_tool = FunctionTool(
    func=book_travel_reservation,
    require_confirmation=needs_booking_confirmation,
)
