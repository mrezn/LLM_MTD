from __future__ import annotations

from pathlib import Path

from llm_mtd_eval.llm_layer.action_adapter import ActionAdapter
from llm_mtd_eval.llm_layer.response_parser import ResponseParser
from llm_mtd_eval.types import (
    ActivePoolState,
    AttackContext,
    ControllerContext,
    LLMDecision,
    NormalizedState,
    QosContext,
    SecurityContext,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        qos_context=QosContext(
            sensor_to_gateway_latency_ms=12.4,
            gateway_to_worker_latency_ms=8.1,
            edge_to_cloud_latency_ms=17.6,
            queue_length=4,
            throughput_bps=123456.0,
            message_loss_rate=0.0,
        ),
        security_context=SecurityContext(
            gateway_seen=True,
            worker_seen=True,
            cloud_seen=False,
            attack_effect_success=False,
            defense_success=False,
        ),
        controller_context=ControllerContext(
            active_policy_actions=1,
            flow_rules_installed=4,
            meters_added=1,
            ryu_apply_duration_ms=22.0,
        ),
        allowed_actions=[
            "observe",
            "quarantine_sensor",
            "rate_limit",
            "reroute_traffic",
            "release_sensor",
        ],
        active_pool=ActivePoolState(enabled=False, active_strategies=[], pool_strategies=[]),
    )


def test_response_parser_accepts_valid_json() -> None:
    parser = ResponseParser(PROJECT_ROOT / "configs" / "schemas" / "llm_decision.schema.json")
    decision = parser.parse(
        """
        prefix text
        {
          "selected_defender_strategy": "rate_limit",
          "target": "sen4",
          "parameters": {"kbps": 128},
          "confidence": 0.78,
          "reasoning_summary": "Throttle traffic before full isolation.",
          "expected_security_gain": 0.63,
          "expected_qos_impact": 0.28,
          "active_pool_update": {"enabled": false, "promote": [], "demote": []}
        }
        suffix text
        """
    )
    assert decision.selected_defender_strategy == "rate_limit"
    assert decision.parameters["kbps"] == 128


def test_action_adapter_falls_back_for_unsupported_action() -> None:
    adapter = ActionAdapter()
    decision = LLMDecision(
        selected_defender_strategy="migrate_worker_traffic",
        target="edge2_vm_s4",
        parameters={},
        confidence=0.66,
        reasoning_summary="Move worker traffic away from the hot path.",
        expected_security_gain=0.55,
        expected_qos_impact=0.31,
        active_pool_update={"enabled": False, "promote": [], "demote": []},
    )
    adapted = adapter.adapt(decision, sample_state())
    assert adapted.executed_action == "observe"
    assert adapted.fallback_used is True
    assert adapted.unsupported_strategy == "migrate_worker_traffic"
