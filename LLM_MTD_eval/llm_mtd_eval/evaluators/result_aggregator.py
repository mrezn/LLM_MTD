"""Phase 3 placeholder for batch result aggregation."""

from __future__ import annotations

from statistics import mean
from typing import Iterable

from ..types import TrialResult


def aggregate_trials(results: Iterable[TrialResult]) -> dict[str, float]:
    result_list = list(results)
    if not result_list:
        return {}
    latencies = [float(item.llm_quality_metrics.get("decision_latency_ms", 0.0)) for item in result_list]
    fallbacks = [float(item.decision.fallback_used) for item in result_list]
    return {
        "trial_count": float(len(result_list)),
        "mean_decision_latency_ms": mean(latencies),
        "fallback_rate": mean(fallbacks),
    }
