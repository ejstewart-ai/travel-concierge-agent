"""Application configuration and strategic model routing.

Centralizes environment settings, model tier routing (Flash for high-throughput
coordination/research/compaction vs. Pro for complex itinerary planning and
self-evaluation), database URLs, and Secret Manager hydration.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.secrets import SecretManagerClient

# Load local .env file if present (never committed to Git).
load_dotenv()

TaskComplexity = Literal["fast", "reasoning"]


class ModelRoutingConfig(BaseModel):
    """Routes each agent role to the optimal Gemini model tier."""

    fast_model: str = Field(
        default="gemini-2.5-flash",
        description=(
            "Low-latency model for coordination, parallel data lookup, "
            "booking handoff, and conversation compaction."
        ),
    )
    reasoning_model: str = Field(
        default="gemini-2.5-pro",
        description=(
            "Higher-capability reasoning model for multi-day itinerary synthesis, "
            "budget tradeoffs, and structured quality self-evaluation."
        ),
    )

    def select_model(self, complexity: TaskComplexity) -> str:
        """Returns the appropriate Gemini model name for the requested complexity tier."""
        if complexity == "reasoning":
            return self.reasoning_model
        return self.fast_model


class AppSettings(BaseModel):
    """Validated runtime settings for the Travel Concierge Agent."""

    app_name: str = Field(
        default="app",
        description="ADK App name; must match the Python package directory ('app').",
    )
    environment: str = Field(default="development")
    use_vertex_ai: bool = Field(default=False)
    google_cloud_project: str | None = Field(default=None)
    google_cloud_location: str = Field(default="us-central1")
    use_secret_manager: bool = Field(default=False)

    models: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig)

    # Persistent storage
    session_database_url: str = Field(
        default="sqlite+aiosqlite:///./data/sessions.db",
        description="Async SQLAlchemy database URL for ADK DatabaseSessionService.",
    )
    traveler_memory_db_path: str = Field(
        default="./data/traveler_memory.db",
        description="Persistent SQLite database path for traveler profiles & memory.",
    )

    # Context compaction & caching
    compaction_interval: int = Field(default=10, ge=1)
    compaction_overlap_size: int = Field(default=2, ge=0)
    compaction_token_threshold: int = Field(default=12000, ge=500)
    compaction_event_retention_size: int = Field(default=4, ge=1)
    context_cache_min_tokens: int = Field(default=2048, ge=0)
    context_cache_ttl_seconds: int = Field(default=1800, ge=60)
    context_cache_intervals: int = Field(default=10, ge=1, le=100)

    # Guardrails & Observability
    enable_cloud_dlp: bool = Field(default=False)
    model_armor_template: str | None = Field(default=None)
    otel_service_name: str = Field(default="travel-concierge-agent")
    otel_exporter_mode: str = Field(default="memory")
    hitl_confirmation_threshold_usd: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Dollar threshold at or above which book_travel_reservation pauses "
            "for explicit human confirmation (0.0 = confirm every booking)."
        ),
    )

    def hydrate_runtime_secrets(
        self, secret_client: SecretManagerClient | None = None
    ) -> None:
        """Loads runtime API keys / DB URLs from Secret Manager if enabled."""
        client = secret_client or SecretManagerClient(
            project_id=self.google_cloud_project,
            use_secret_manager=self.use_secret_manager,
        )
        if not self.use_vertex_ai and not os.environ.get("GOOGLE_API_KEY"):
            api_key = client.get_secret("google-api-key", env_var="GOOGLE_API_KEY")
            if api_key:
                os.environ["GOOGLE_API_KEY"] = api_key

        db_secret = client.get_secret(
            "session-database-url", env_var="SESSION_DATABASE_URL"
        )
        if db_secret:
            self.session_database_url = db_secret


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Builds and caches validated AppSettings from environment variables."""
    settings = AppSettings(
        environment=os.environ.get("ENVIRONMENT", "development"),
        use_vertex_ai=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "false").lower()
        == "true",
        google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None,
        google_cloud_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        use_secret_manager=os.environ.get("USE_SECRET_MANAGER", "false").lower()
        == "true",
        models=ModelRoutingConfig(
            fast_model=os.environ.get("TRAVEL_FAST_MODEL", "gemini-2.5-flash"),
            reasoning_model=os.environ.get("TRAVEL_REASONING_MODEL", "gemini-2.5-pro"),
        ),
        session_database_url=os.environ.get(
            "SESSION_DATABASE_URL", "sqlite+aiosqlite:///./data/sessions.db"
        ),
        traveler_memory_db_path=os.environ.get(
            "TRAVELER_MEMORY_DB_PATH", "./data/traveler_memory.db"
        ),
        enable_cloud_dlp=os.environ.get("ENABLE_CLOUD_DLP", "false").lower() == "true",
        model_armor_template=os.environ.get("MODEL_ARMOR_TEMPLATE") or None,
        otel_service_name=os.environ.get(
            "OTEL_SERVICE_NAME", "travel-concierge-agent"
        ),
        otel_exporter_mode=os.environ.get("OTEL_EXPORTER_MODE", "memory"),
        hitl_confirmation_threshold_usd=float(
            os.environ.get("HITL_CONFIRMATION_THRESHOLD_USD", "0.0")
        ),
    )
    settings.hydrate_runtime_secrets()
    return settings
