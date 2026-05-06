from __future__ import annotations

from llm_mtd_eval.llm_layer.constraint_guard import ConstraintGuard
from llm_mtd_eval.types import (
    ActivePoolState,
    AttackContext,
    ControllerContext,
    LLMDecision,
    NormalizedState,
    QosContext,
    SecurityContext,
)


def sample_state() -> NormalizedState:
    return NormalizedState(
        scenario_id="sen4_edge2_clouddb",
        timestamp="2026-04-21T12:00:00Z",
        target_asset="cloud_db",
        entry_node="sen4",
        attack_context=AttackContext(
            mulval_path=["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
            risk_score=0.82,
            caldera_result=None,
        ),
        qos_context=QosContext(),
        security_context=SecurityContext(),
        controller_context=ControllerContext(),
        allowed_actions=[
            "observe",
            "quarantine_sensor",
            "rate_limit",
            "reroute_traffic",
            "release_sensor",
        ],
        active_pool=ActivePoolState(enabled=False, active_strategies=[], pool_strategies=[]),
    )


def test_constraint_guard_rejects_invalid_rate_limit_parameters() -> None:
    guard = ConstraintGuard()
    decision = LLMDecision(
        selected_defender_strategy="rate_limit",
        target="sen4",
        parameters={"kbps": 0},
        confidence=0.9,
        reasoning_summary="Rate limiting is required.",
        expected_security_gain=0.7,
        expected_qos_impact=0.3,
        active_pool_update={"enabled": False, "promote": [], "demote": []},
    )
    result = guard.evaluate(decision, sample_state())
    assert result.executable is False
    assert any("positive kbps" in issue for issue in result.issues)
