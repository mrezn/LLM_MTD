from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate

from llm_mtd_eval.types import TrialDecisionSummary, TrialResult, TrialTimestamps


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_trial_result_matches_schema() -> None:
    result = TrialResult(
        trial_id="trial_0001",
        scenario_id="sen4_edge2_clouddb",
        model_type="llm_only",
        seed=42,
        timestamps=TrialTimestamps(
            started_at="2026-04-21T12:00:00Z",
            decision_at="2026-04-21T12:00:02Z",
            executed_at="2026-04-21T12:00:03Z",
            observed_at="2026-04-21T12:00:08Z",
        ),
        decision=TrialDecisionSummary(
            recommended_strategy="rate_limit",
            executed_action="rate_limit",
            fallback_used=False,
            unsupported_strategy=None,
        ),
        qos_metrics={"sensor_to_gateway_latency_ms_before": 12.4},
        security_metrics={"gateway_seen_before": 1.0},
        overhead_metrics={"ryu_apply_duration_ms_before": 22.0},
        llm_quality_metrics={"valid_json_rate": 1.0},
        raw_state_before={},
        raw_state_after={},
        notes=[],
    )
    schema = json.loads(
        (PROJECT_ROOT / "configs" / "schemas" / "trial_result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validate(instance=result.model_dump(mode="json"), schema=schema)
