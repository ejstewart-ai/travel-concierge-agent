"""Strict Pydantic v2 JSON schemas for tool inputs, tool outputs, and agent self-evaluation.

Every tool validates incoming LLM arguments against an explicit request schema
and serializes structured response schemas (or guided error-recovery payloads)
so model behavior stays tightly constrained and predictable.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IATA_RE = re.compile(r"^[A-Z]{3}$")


class ToolErrorResponse(BaseModel):
    """Structured error payload with actionable recovery guidance for the LLM."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["error"] = "error"
    error_code: str = Field(description="Machine-readable error identifier.")
    message: str = Field(description="Human-readable explanation of what failed.")
    recovery_steps: list[str] = Field(
        description="Concrete next steps the agent should take to recover."
    )


class FlightSearchRequest(BaseModel):
    """Validated input schema for searching available flights."""

    model_config = ConfigDict(extra="forbid")

    origin_iata: str = Field(
        description="3-letter uppercase IATA airport code for departure (e.g., SFO)."
    )
    destination_iata: str = Field(
        description="3-letter uppercase IATA airport code for arrival (e.g., HND)."
    )
    departure_date: date = Field(description="Departure date in YYYY-MM-DD format.")
    return_date: date = Field(description="Return date in YYYY-MM-DD format.")
    cabin_class: Literal["economy", "premium_economy", "business"] = Field(
        description="Desired airline cabin class."
    )
    max_price_usd: float = Field(
        gt=0, le=50000, description="Maximum acceptable round-trip price in USD."
    )

    @field_validator("origin_iata", "destination_iata", mode="before")
    @classmethod
    def _normalize_iata(cls, value: str) -> str:
        normalized = str(value).strip().upper()
        if not _IATA_RE.match(normalized):
            raise ValueError(
                f"Invalid IATA code '{value}'. Must be exactly 3 letters (e.g., SFO, JFK, HND)."
            )
        return normalized

    @model_validator(mode="after")
    def _validate_dates_and_route(self) -> FlightSearchRequest:
        if self.origin_iata == self.destination_iata:
            raise ValueError("Origin and destination IATA airport codes must be different.")
        if self.return_date <= self.departure_date:
            raise ValueError("Return date must be strictly after departure date.")
        return self


class FlightOption(BaseModel):
    """Single round-trip flight option."""

    model_config = ConfigDict(extra="forbid")

    flight_id: str
    airline: str
    origin_iata: str
    destination_iata: str
    departure_date: str
    return_date: str
    cabin_class: str
    nonstop: bool
    duration_hours: float
    price_usd: float


class FlightSearchResponse(BaseModel):
    """Validated output schema returned by search_flight_options."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    origin_iata: str
    destination_iata: str
    options: list[FlightOption]
    currency: Literal["USD"] = "USD"


class HotelSearchRequest(BaseModel):
    """Validated input schema for searching hotel accommodations."""

    model_config = ConfigDict(extra="forbid")

    destination_city: str = Field(
        min_length=2, max_length=80, description="Target city name (e.g., Tokyo, Paris, Lisbon)."
    )
    check_in_date: date = Field(description="Check-in date in YYYY-MM-DD format.")
    check_out_date: date = Field(description="Check-out date in YYYY-MM-DD format.")
    max_nightly_rate_usd: float = Field(
        gt=0, le=10000, description="Maximum nightly room rate in USD."
    )
    preferred_style: Literal["boutique", "luxury", "budget", "business", "any"] = Field(
        description="Preferred accommodation style."
    )

    @model_validator(mode="after")
    def _validate_stay_dates(self) -> HotelSearchRequest:
        if self.check_out_date <= self.check_in_date:
            raise ValueError("Check-out date must be strictly after check-in date.")
        return self


class HotelOption(BaseModel):
    """Single hotel accommodation listing."""

    model_config = ConfigDict(extra="forbid")

    hotel_id: str
    name: str
    city: str
    neighborhood: str
    style: str
    rating: float = Field(ge=1.0, le=5.0)
    nightly_rate_usd: float
    total_stay_usd: float
    highlights: list[str]


class HotelSearchResponse(BaseModel):
    """Validated output schema returned by search_hotel_accommodations."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    destination_city: str
    nights: int
    options: list[HotelOption]


class DestinationAdvisoryRequest(BaseModel):
    """Validated input schema for destination advisory lookup."""

    model_config = ConfigDict(extra="forbid")

    destination_city: str = Field(
        min_length=2, max_length=80, description="Destination city to inspect."
    )
    travel_month: str = Field(
        min_length=3, max_length=20, description="Month of travel (e.g., October)."
    )


class DestinationAdvisoryResponse(BaseModel):
    """Validated output schema for destination travel advisory information."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    destination_city: str
    country: str
    safety_level: Literal[
        "Level 1 - Exercise Normal Precautions",
        "Level 2 - Exercise Increased Caution",
    ]
    visa_guidance: str
    seasonal_weather: str
    local_tips: list[str]


class SavePreferenceRequest(BaseModel):
    """Validated input schema for persisting a traveler preference."""

    model_config = ConfigDict(extra="forbid")

    traveler_id: str = Field(min_length=1, max_length=64)
    category: Literal[
        "hotel_style",
        "airline_seating",
        "dietary",
        "budget_tier",
        "pace",
        "interests",
        "general",
    ]
    preference_value: str = Field(min_length=1, max_length=300)


class SavePreferenceResponse(BaseModel):
    """Validated output schema after persisting a scrubbed traveler preference."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    traveler_id: str
    category: str
    stored_value: str
    pii_was_redacted: bool


class RetrieveProfileRequest(BaseModel):
    """Validated input schema for retrieving traveler preferences and past memories."""

    model_config = ConfigDict(extra="forbid")

    traveler_id: str = Field(min_length=1, max_length=64)
    query: str = Field(
        min_length=1,
        max_length=200,
        description="Search topic used to rank relevant long-term travel memories.",
    )


class RetrieveProfileResponse(BaseModel):
    """Validated output schema containing persisted preferences and top memory matches."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success"] = "success"
    traveler_id: str
    preferences: dict[str, str]
    relevant_memories: list[str]


class BookingRequest(BaseModel):
    """Validated input schema for high-stakes travel booking execution."""

    model_config = ConfigDict(extra="forbid")

    item_type: Literal["flight", "hotel"]
    item_id: str = Field(
        min_length=3, max_length=32, description="Catalog ID (e.g., FL-SFO-HND-101)."
    )
    traveler_name: str = Field(min_length=2, max_length=100)
    total_cost_usd: float = Field(gt=0, le=50000)
    trip_dates: str = Field(
        min_length=8,
        max_length=40,
        description="Date range string (e.g., 2026-10-10 to 2026-10-14).",
    )


class BookingConfirmation(BaseModel):
    """Validated output schema returned after human-approved booking execution."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "awaiting_human_approval", "rejected"]
    confirmation_code: str | None = None
    item_type: str
    item_id: str
    total_cost_usd: float
    message: str


class ItineraryQualityReview(BaseModel):
    """Structured self-evaluation schema enforced on itinerary_quality_reviewer."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "revise"] = Field(
        description="Whether the draft itinerary satisfies budget, date, and safety checks."
    )
    budget_compliant: bool = Field(
        description="True if estimated flight + hotel costs stay within user budget."
    )
    dates_consistent: bool = Field(
        description="True if all flight and hotel dates align cleanly with the requested window."
    )
    policy_compliant: bool = Field(
        description="True if no unapproved bookings or fabricated prices appear in the draft."
    )
    score_out_of_10: int = Field(ge=1, le=10, description="Overall itinerary quality score.")
    improvement_notes: list[str] = Field(
        description="Actionable feedback if improvements are recommended."
    )
    final_traveler_summary: str = Field(
        description="Polished, ready-to-read travel recommendation summarizing the verified plan."
    )
