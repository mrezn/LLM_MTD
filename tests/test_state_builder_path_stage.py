from __future__ import annotations

import json

import pytest

from game.state_builder import (
    build_state,
    current_path_risk_details,
    derive_cloud_seen,
    query_ryu_defense_state,
)


def _scenario_file(tmp_path):
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps([
        {
            "scenario_id": "sen4_edge2_clouddb",
            "entry_node": "sen4",
            "target_asset": "cloud_db",
            "mulval_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        }
    ]), encoding="utf-8")
    return path


def _policy_file(tmp_path, risk_path=None):
    risk_path = risk_path or ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"]
    path = tmp_path / "policy.json"
    key = "->".join(risk_path)
    path.write_text(json.dumps({
        "scenario_id": "sen4_edge2_clouddb",
        "attack_paths": [risk_path],
        "path_risk_scores": {key: 0.82},
    }), encoding="utf-8")
    return path


def _core_data(*, attack_events=None, worker_requests=0, cloud_storage=1998):
    return {
        "attack_events": attack_events or [],
        "defense_events": [],
        "message_loss_counters": [
            {"source": "edge2_vm_s4", "metric": "requests_total", "role": "edge_worker", "value": worker_requests},
            {"source": "cloud_db", "metric": "storage_confirmations_total", "role": "cloud_db", "value": cloud_storage},
        ],
        "throughput": [],
        "resource_use": [],
        "sensor_to_edge_latency_ms": [],
        "edge_to_cloud_latency_ms": [],
    }


def test_background_storage_without_baseline_does_not_jump_to_cloud(tmp_path):
    state = build_state(
        core_data=_core_data(),
        mtd_metrics_text="",
        mtd_status_data={},
        scenario_registry_path=_scenario_file(tmp_path),
        mulval_policy_path=_policy_file(tmp_path),
        constraints={},
    )
    assert state["path_stage"] == 0
    assert state["raw_path_stage"] == 0
    assert state["path_evidence"]["cloud_seen"] is False
    assert state["workload"]["cloud_storage_confirmations"] == 1998


def test_background_storage_small_delta_does_not_trigger_cloud_seen():
    cloud = derive_cloud_seen(
        attack_metrics={},
        cloud_storage_current=1998,
        constraints={"cloud_storage_baseline": 1997},
        has_attack_context=True,
    )
    assert cloud["cloud_seen"] is False
    assert cloud["cloud_storage_delta"] == 1


def test_exfil_confirmation_triggers_cloud_seen():
    cloud = derive_cloud_seen(
        attack_metrics={"cloud_exfil_confirmations_total": 1},
        cloud_storage_current=2005,
        constraints={"cloud_storage_baseline": 1997},
        has_attack_context=True,
    )
    assert cloud["cloud_seen"] is True
    assert cloud["cloud_seen_reason"] == "cloud_exfil_confirmations"


def test_exact_mulval_path_match_required():
    details = current_path_risk_details(
        ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        [{"path": ["sen4", "edge2_gw", "cloud_db"], "risk_score": 0.74}],
    )
    assert details["current_path_risk"] == 0.50
    assert details["mulval_path_mismatch"] is True
    assert details["mulval_match_type"] == "missing_exact_path"


def test_strict_preconditions_fail_on_mulval_path_mismatch(tmp_path):
    with pytest.raises(RuntimeError):
        build_state(
            core_data=_core_data(),
            mtd_metrics_text="",
            mtd_status_data={},
            scenario_registry_path=_scenario_file(tmp_path),
            mulval_policy_path=_policy_file(tmp_path, risk_path=["sen4", "edge2_gw", "cloud_db"]),
            constraints={"strict_preconditions": True},
        )


def test_query_ryu_defense_state_prefers_live_ryu_over_stale_metrics():
    result = query_ryu_defense_state(
        mtd_status_data={"active_actions": [{"action": "quarantine_sensor", "target": "sen4"}]},
        controller_metrics={"ryu_controller_flow_rules_installed_total": 20},
        defense_metrics={"drop_rules_active": False, "defense_success": False},
    )
    assert result["drop_rules_active"] is True
    assert result["active_policy_actions"] == ["quarantine_sensor:sen4"]


def test_gateway_and_worker_evidence_map_to_expected_stages(tmp_path):
    scenario = _scenario_file(tmp_path)
    policy = _policy_file(tmp_path)
    gateway_state = build_state(
        core_data=_core_data(attack_events=[{"source": "sen4_edge2_clouddb", "metric": "gateway_seen", "value": 1}]),
        mtd_metrics_text="",
        mtd_status_data={},
        scenario_registry_path=scenario,
        mulval_policy_path=policy,
        constraints={},
    )
    worker_state = build_state(
        core_data=_core_data(attack_events=[{"source": "sen4_edge2_clouddb", "metric": "worker_seen", "value": 1}], worker_requests=3),
        mtd_metrics_text="",
        mtd_status_data={},
        scenario_registry_path=scenario,
        mulval_policy_path=policy,
        constraints={},
    )
    assert gateway_state["path_stage"] == 1
    assert worker_state["path_stage"] == 2
