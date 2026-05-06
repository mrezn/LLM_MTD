from __future__ import annotations

from llm_mtd_eval.evaluators.run_trial import _recover_decision_from_text
from llm_mtd_eval.types import (
    ActivePoolState,
    AttackContext,
    ControllerContext,
    NormalizedState,
    QosContext,
    SecurityContext,
)


def sample_state() -> NormalizedState:
    return NormalizedState(
        scenario_id="sen4_edge2_clouddb",
        timestamp="2026-04-23T00:00:00Z",
        target_asset="cloud_db",
        entry_node="sen4",
        attack_context=AttackContext(
            mulval_path=["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
            risk_score=0.5,
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


def test_recover_decision_from_malformed_llm_text() -> None:
    recovered = _recover_decision_from_text(
        (
            "We need to choose one defender action. The best action is observe. "
            "Confidence maybe 0.9. expected_security_gain 0.0. "
            'expected_qos_impact 0.0. Provide reasoning summary: "No evidence of attack, observe to gather more info."'
        ),
        sample_state(),
    )
    assert recovered is not None
    assert recovered.selected_defender_strategy == "observe"
    assert recovered.target == ""
    assert recovered.confidence == 0.9
    assert recovered.expected_security_gain == 0.0
    assert recovered.expected_qos_impact == 0.0
