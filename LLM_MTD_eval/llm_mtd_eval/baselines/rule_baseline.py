from __future__ import annotations

from ..types import ActivePoolUpdate, LLMDecision, NormalizedState


def select_rule_baseline(state: NormalizedState) -> LLMDecision:
    if state.qos_context.queue_length >= 5 or state.qos_context.message_loss_rate > 0.05:
        action = "rate_limit"
        target = state.entry_node
        parameters = {"kbps": 128}
        reason = "Rule baseline throttles traffic when queue or loss rises."
        security_gain = 0.58
        qos_impact = 0.3
    else:
        action = "observe"
        target = ""
        parameters = {}
        reason = "Rule baseline remains in observe mode."
        security_gain = 0.1
        qos_impact = 0.02
    return LLMDecision(
        selected_defender_strategy=action,
        target=target,
        parameters=parameters,
        confidence=0.65,
        reasoning_summary=reason,
        expected_security_gain=security_gain,
        expected_qos_impact=qos_impact,
        active_pool_update=ActivePoolUpdate(),
    )
