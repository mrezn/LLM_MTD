from __future__ import annotations

from ..types import ActionAdaptation, LLMDecision, NormalizedState
from .constraint_guard import ConstraintGuard


class ActionAdapter:
    def __init__(self, guard: ConstraintGuard | None = None) -> None:
        self.guard = guard or ConstraintGuard()

    def adapt(self, decision: LLMDecision, state: NormalizedState) -> ActionAdaptation:
        evaluation = self.guard.evaluate(decision, state)
        if not evaluation.executable:
            payload = {
                "action": "observe",
                "target": decision.target,
                "not_executed_reason": "constraint_guard_rejected",
                "original_strategy": decision.selected_defender_strategy,
            }
            return ActionAdaptation(
                recommended_strategy=decision.selected_defender_strategy,
                executed_action="observe",
                target=decision.target,
                payload=payload,
                fallback_used=True,
                unsupported_strategy=decision.selected_defender_strategy,
                not_executed_reason="constraint_guard_rejected",
                notes=list(evaluation.issues),
            )

        payload = {
            "action": decision.selected_defender_strategy,
            "target": decision.target,
            **evaluation.sanitized_parameters,
        }
        if decision.selected_defender_strategy == "observe" and not decision.target:
            payload = {"action": "observe"}
        return ActionAdaptation(
            recommended_strategy=decision.selected_defender_strategy,
            executed_action=decision.selected_defender_strategy,
            target=decision.target,
            payload=payload,
            fallback_used=False,
            unsupported_strategy=None,
            notes=[],
        )
