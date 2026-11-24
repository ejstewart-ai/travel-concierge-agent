"""Multi-agent ADK architecture and App assembly for the Travel Concierge Agent.

Orchestration Topology:
- `travel_concierge_coordinator` (LlmAgent, fast model): entry point, preference memory,
  and delegation to specialists.
- `trip_planning_pipeline` (SequentialAgent):
    1. `parallel_travel_research` (ParallelAgent, fast model): concurrent flight, hotel,
       and destination advisory research.
    2. `itinerary_architect_agent` (LlmAgent, reasoning model): synthesizes findings into
       a budget-aware day-by-day plan.
    3. `itinerary_quality_reviewer` (LlmAgent, reasoning model, strict Pydantic output):
       self-evaluates budget, dates, and policy compliance.
- `booking_execution_agent` (LlmAgent, fast model): executes reservations behind ADK's
  Human-in-the-Loop confirmation gate (`require_confirmation`).

App-Level Capabilities:
- `EventsCompactionConfig` + `LlmEventSummarizer` (history compaction)
- `ContextCacheConfig` (context caching)
- `ResumabilityConfig(is_resumable=True)` (pausing/resuming HITL booking approvals)
- `TravelSafetyAndPolicyPlugin` & `ObservabilityAndAuditPlugin`
"""

from __future__ import annotations

from google.adk.agents import Agent, ParallelAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import App, EventsCompactionConfig, ResumabilityConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types as genai_types

from app.config import AppSettings, get_settings
from app.memory import async_memory_after_agent_callback
from app.observability.tracing import configure_tracing
from app.plugins.observability_plugin import ObservabilityAndAuditPlugin
from app.plugins.safety_plugin import TravelSafetyAndPolicyPlugin
from app.prompts import (
    BOOKING_SPECIALIST_INSTRUCTION,
    COORDINATOR_CONSTITUTION,
    DESTINATION_ADVISORY_INSTRUCTION,
    FLIGHT_RESEARCH_INSTRUCTION,
    HOTEL_RESEARCH_INSTRUCTION,
    ITINERARY_ARCHITECT_INSTRUCTION,
    ITINERARY_QUALITY_REVIEWER_INSTRUCTION,
)
from app.schemas import ItineraryQualityReview
from app.tools import (
    book_travel_reservation_tool,
    lookup_destination_travel_advisory_tool,
    retrieve_traveler_profile_tool,
    save_traveler_preference_tool,
    search_flight_options_tool,
    search_hotel_accommodations_tool,
)


def create_flight_research_agent(settings: AppSettings) -> Agent:
    """Creates the fast-tier Flight Research sub-agent."""
    return Agent(
        name="flight_research_agent",
        model=settings.models.select_model("fast"),
        description="Searches round-trip flights matching origin, destination, dates, and budget.",
        instruction=FLIGHT_RESEARCH_INSTRUCTION,
        tools=[search_flight_options_tool],
        output_key="flight_research_findings",
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
    )


def create_hotel_research_agent(settings: AppSettings) -> Agent:
    """Creates the fast-tier Hotel Research sub-agent."""
    return Agent(
        name="hotel_research_agent",
        model=settings.models.select_model("fast"),
        description=(
            "Searches hotels matching destination city, stay dates, style, and nightly rate."
        ),
        instruction=HOTEL_RESEARCH_INSTRUCTION,
        tools=[search_hotel_accommodations_tool],
        output_key="hotel_research_findings",
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
    )


def create_destination_advisory_agent(settings: AppSettings) -> Agent:
    """Creates the fast-tier Destination Advisory sub-agent."""
    return Agent(
        name="destination_advisory_agent",
        model=settings.models.select_model("fast"),
        description="Looks up travel safety advisories, visa guidance, and seasonal weather tips.",
        instruction=DESTINATION_ADVISORY_INSTRUCTION,
        tools=[lookup_destination_travel_advisory_tool],
        output_key="destination_advisory_findings",
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.1),
    )


def create_itinerary_architect_agent(settings: AppSettings) -> Agent:
    """Creates the reasoning-tier Itinerary Architect sub-agent."""
    return Agent(
        name="itinerary_architect_agent",
        model=settings.models.select_model("reasoning"),
        description="Synthesizes parallel flight, hotel, and advisory data into a day-by-day plan.",
        instruction=ITINERARY_ARCHITECT_INSTRUCTION,
        output_key="draft_itinerary",
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.3),
    )


def create_itinerary_quality_reviewer(settings: AppSettings) -> Agent:
    """Creates the reasoning-tier Self-Evaluation Reviewer with strict Pydantic output."""
    return Agent(
        name="itinerary_quality_reviewer",
        model=settings.models.select_model("reasoning"),
        description="Self-evaluates draft itineraries against budget, date, and safety policies.",
        instruction=ITINERARY_QUALITY_REVIEWER_INSTRUCTION,
        output_schema=ItineraryQualityReview,
        output_key="itinerary_quality_review",
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.0),
    )


def create_trip_planning_pipeline(settings: AppSettings) -> SequentialAgent:
    """Creates the Sequential(Parallel Research -> Architect -> Self-Eval) workflow."""
    parallel_research = ParallelAgent(
        name="parallel_travel_research",
        description="Runs flight, hotel, and destination advisory research concurrently.",
        sub_agents=[
            create_flight_research_agent(settings),
            create_hotel_research_agent(settings),
            create_destination_advisory_agent(settings),
        ],
    )
    return SequentialAgent(
        name="trip_planning_pipeline",
        description=(
            "End-to-end trip planning workflow: parallel research, Pro-model itinerary "
            "synthesis, and structured self-evaluation quality check."
        ),
        sub_agents=[
            parallel_research,
            create_itinerary_architect_agent(settings),
            create_itinerary_quality_reviewer(settings),
        ],
    )


def create_booking_execution_agent(settings: AppSettings) -> Agent:
    """Creates the HITL-guarded Booking Execution Specialist sub-agent."""
    return Agent(
        name="booking_execution_agent",
        model=settings.models.select_model("fast"),
        description=(
            "Handles confirmed flight or hotel reservation requests behind a mandatory "
            "Human-in-the-Loop approval pause."
        ),
        instruction=BOOKING_SPECIALIST_INSTRUCTION,
        tools=[book_travel_reservation_tool],
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.0),
    )


def create_root_agent(settings: AppSettings | None = None) -> Agent:
    """Creates the root Travel Concierge Coordinator and its specialist sub-agents."""
    cfg = settings or get_settings()
    return Agent(
        name="travel_concierge_coordinator",
        model=cfg.models.select_model("fast"),
        description=(
            "Lead Travel Concierge Coordinator managing traveler memory, trip planning "
            "delegation, and human-approved bookings."
        ),
        instruction=COORDINATOR_CONSTITUTION,
        tools=[
            PreloadMemoryTool(),
            save_traveler_preference_tool,
            retrieve_traveler_profile_tool,
        ],
        sub_agents=[
            create_trip_planning_pipeline(cfg),
            create_booking_execution_agent(cfg),
        ],
        after_agent_callback=async_memory_after_agent_callback,
        generate_content_config=genai_types.GenerateContentConfig(temperature=0.2),
    )


def create_app(settings: AppSettings | None = None) -> App:
    """Assembles the production ADK App with compaction, caching, HITL resumability, and plugins."""
    cfg = settings or get_settings()
    configure_tracing(
        service_name=cfg.otel_service_name,
        exporter_mode=cfg.otel_exporter_mode,
        project_id=cfg.google_cloud_project,
    )

    safety_plugin = TravelSafetyAndPolicyPlugin(
        model_armor_template=cfg.model_armor_template,
    )
    observability_plugin = ObservabilityAndAuditPlugin()

    return App(
        name=cfg.app_name,
        root_agent=create_root_agent(cfg),
        plugins=[safety_plugin, observability_plugin],
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=cfg.compaction_interval,
            overlap_size=cfg.compaction_overlap_size,
            summarizer=LlmEventSummarizer(
                llm=Gemini(model=cfg.models.select_model("fast"))
            ),
        ),
        context_cache_config=ContextCacheConfig(
            min_tokens=cfg.context_cache_min_tokens,
            ttl_seconds=cfg.context_cache_ttl_seconds,
            cache_intervals=cfg.context_cache_intervals,
        ),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


# Standard ADK discovery symbols (`app` and `root_agent`) for `adk web`, `adk eval`, and FastAPI.
app = create_app()
root_agent = app.root_agent
