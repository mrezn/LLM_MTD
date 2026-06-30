from __future__ import annotations

from eval.types import ActionAdaptation, LLMDecision, LLMResponseTrace


def compute_llm_quality_metrics(
    *,
    decision: LLMDecision,
    trace: LLMResponseTrace,
    adaptation: ActionAdaptation,
) -> dict[str, float]:
    reasoning = decision.reasoning_summary.lower()
    recommended = decision.selected_defender_strategy.lower()
    mismatch = 0.0 if recommended.replace("_", " ") in reasoning or recommended == "observe" else 1.0
    return {
        "valid_json_rate": 1.0,
        "valid_executable_action_rate": 0.0 if adaptation.fallback_used else 1.0,
        "unsupported_action_rate": 1.0 if adaptation.unsupported_strategy else 0.0,
        "fallback_to_observe_rate": 1.0 if adaptation.fallback_used else 0.0,
        "decision_latency_ms": trace.latency_ms,
        "repeated_run_consistency": 1.0,
        "reasoning_action_mismatch_rate": mismatch,
    }
