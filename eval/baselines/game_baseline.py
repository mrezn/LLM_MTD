from __future__ import annotations

from eval.types import ActivePoolUpdate, LLMDecision, NormalizedState


def select_game_baseline(state: NormalizedState) -> LLMDecision:
    if state.security_context.cloud_seen or state.security_context.attack_effect_success:
        return LLMDecision(
            selected_defender_strategy="quarantine_sensor",
            target=state.entry_node,
            parameters={},
            confidence=0.7,
            reasoning_summary="Baseline game heuristic escalates to isolation at high attack pressure.",
            expected_security_gain=0.75,
            expected_qos_impact=0.6,
            active_pool_update=ActivePoolUpdate(),
        )
    return LLMDecision(
        selected_defender_strategy="observe",
        target="",
        parameters={},
        confidence=0.55,
        reasoning_summary="Baseline game heuristic does not escalate yet.",
        expected_security_gain=0.1,
        expected_qos_impact=0.02,
        active_pool_update=ActivePoolUpdate(),
    )
