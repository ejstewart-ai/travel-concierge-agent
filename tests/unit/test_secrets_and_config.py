"""Unit tests for SecretManagerClient, strategic model routing, and ADK App assembly."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents import ParallelAgent, SequentialAgent

from app.agent import create_app
from app.config import AppSettings, ModelRoutingConfig
from app.secrets import SecretManagerClient, SecretResolutionError


def test_secret_manager_client_reads_gcp_secret_and_rejects_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "your-gemini-api-key-here")

    class _FakeSecretClient:
        def access_secret_version(self, request: dict[str, str]) -> SimpleNamespace:
            assert request["name"] == "projects/demo-proj/secrets/google-api-key/versions/latest"
            return SimpleNamespace(
                payload=SimpleNamespace(data=b"real-secret-value-from-gsm")
            )

    client = SecretManagerClient(
        project_id="demo-proj",
        use_secret_manager=True,
        gcp_client=_FakeSecretClient(),
    )
    resolved = client.get_secret("google-api-key", env_var="GOOGLE_API_KEY", required=True)
    assert resolved == "real-secret-value-from-gsm"


def test_secret_manager_client_raises_on_missing_required_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NONEXISTENT_API_KEY", raising=False)
    client = SecretManagerClient(use_secret_manager=False)
    with pytest.raises(SecretResolutionError):
        client.get_secret("nonexistent-api-key", required=True)


def test_strategic_model_routing_and_app_topology() -> None:
    settings = AppSettings(
        models=ModelRoutingConfig(
            fast_model="gemini-2.5-flash",
            reasoning_model="gemini-2.5-pro",
        )
    )
    adk_app = create_app(settings)

    # App-level AgentOps features
    assert adk_app.name == "app"
    assert adk_app.events_compaction_config is not None
    assert adk_app.context_cache_config is not None
    assert adk_app.resumability_config is not None
    assert adk_app.resumability_config.is_resumable is True
    assert len(adk_app.plugins) == 2

    # Multi-agent hierarchy & strategic model routing verification
    coordinator = adk_app.root_agent
    assert coordinator.name == "travel_concierge_coordinator"
    assert coordinator.model == "gemini-2.5-flash"

    pipeline = coordinator.sub_agents[0]
    booking_agent = coordinator.sub_agents[1]
    assert isinstance(pipeline, SequentialAgent)
    assert booking_agent.name == "booking_execution_agent"
    assert booking_agent.model == "gemini-2.5-flash"

    parallel_research = pipeline.sub_agents[0]
    architect = pipeline.sub_agents[1]
    reviewer = pipeline.sub_agents[2]
    assert isinstance(parallel_research, ParallelAgent)
    assert len(parallel_research.sub_agents) == 3
    for researcher in parallel_research.sub_agents:
        assert researcher.model == "gemini-2.5-flash"

    assert architect.name == "itinerary_architect_agent"
    assert architect.model == "gemini-2.5-pro"
    assert reviewer.name == "itinerary_quality_reviewer"
    assert reviewer.model == "gemini-2.5-pro"
    assert reviewer.output_schema is not None
