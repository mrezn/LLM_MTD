from __future__ import annotations

from eval.types import ActionAdaptation, LLMDecision, LLMResponseTrace, NormalizedState
from .llm_quality_metrics import compute_llm_quality_metrics
from .overhead_metrics import compute_overhead_metrics
from .qos_metrics import compute_qos_metrics
from .security_metrics import compute_security_metrics


def collect_metrics(
    *,
    before: NormalizedState,
    after: NormalizedState | None,
    decision: LLMDecision,
    trace: LLMResponseTrace,
    adaptation: ActionAdaptation,
) -> dict[str, dict[str, float]]:
    return {
        "qos_metrics": compute_qos_metrics(before, after),
        "security_metrics": compute_security_metrics(before, after),
        "overhead_metrics": compute_overhead_metrics(before, after),
        "llm_quality_metrics": compute_llm_quality_metrics(
            decision=decision,
            trace=trace,
            adaptation=adaptation,
        ),
    }
