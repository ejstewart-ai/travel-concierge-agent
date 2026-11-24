"""Automated evaluation suite harness validating golden datasets and optional live ADK evals.

Static schema validation runs automatically in CI without external API keys.
Set `RUN_LIVE_EVALS=1` with a valid Gemini API key or Vertex AI credentials to
execute the full ADK `AgentEvaluator` benchmark against live Gemini models.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from google.adk.evaluation.eval_set import EvalSet

EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_EVALSET_PATH = EVAL_DIR / "evalsets" / "travel_concierge_golden.evalset.json"
EVAL_CONFIG_PATH = EVAL_DIR / "eval_config.json"


def test_golden_evalset_conforms_to_adk_schema() -> None:
    """Verifies the golden evaluation dataset parses cleanly against ADK's EvalSet schema."""
    raw_data = json.loads(GOLDEN_EVALSET_PATH.read_text(encoding="utf-8"))
    eval_set = EvalSet.model_validate(raw_data)

    assert eval_set.eval_set_id == "travel_concierge_golden_v1"
    assert len(eval_set.eval_cases) >= 5

    case_ids = {case.eval_id for case in eval_set.eval_cases}
    assert "case_01_tokyo_multi_agent_trip_plan" in case_ids
    assert "case_03_guided_error_recovery_past_dates" in case_ids
    assert "case_04_safety_guardrail_prompt_injection_refusal" in case_ids
    assert "case_05_hitl_booking_request_triggers_confirmation" in case_ids


def test_eval_config_defines_required_agentops_metrics() -> None:
    """Ensures eval_config.json specifies trajectory, safety, and rubric quality thresholds."""
    config = json.loads(EVAL_CONFIG_PATH.read_text(encoding="utf-8"))
    criteria = config["criteria"]

    assert "tool_trajectory_avg_score" in criteria
    assert "safety_v1" in criteria
    assert "rubric_based_final_response_quality_v1" in criteria
    assert len(criteria["rubric_based_final_response_quality_v1"]["rubrics"]) >= 3


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_EVALS") != "1",
    reason="Set RUN_LIVE_EVALS=1 with Gemini/Vertex AI credentials to run live LLM evals.",
)
async def test_run_live_adk_golden_evaluation() -> None:
    """Runs the live ADK AgentEvaluator against the golden travel concierge dataset."""
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    await AgentEvaluator.evaluate(
        agent_module="app",
        eval_dataset_file_path_or_dir=str(GOLDEN_EVALSET_PATH),
        num_runs=1,
    )
