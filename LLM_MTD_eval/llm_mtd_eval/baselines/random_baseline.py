from __future__ import annotations

import random

from ..types import ActivePoolUpdate, LLMDecision, NormalizedState


def select_random_baseline(state: NormalizedState, seed: int | None = None) -> LLMDecision:
    rng = random.Random(seed)
    action = rng.choice(state.allowed_actions)
    target = state.entry_node if action != "observe" else ""
    parameters = {"kbps": 128} if action == "rate_limit" else {}
    return LLMDecision(
        selected_defender_strategy=action,
        target=target,
        parameters=parameters,
        confidence=0.34,
        reasoning_summary="Random baseline sampled one allowed executable action.",
        expected_security_gain=0.2,
        expected_qos_impact=0.2,
        active_pool_update=ActivePoolUpdate(),
    )
