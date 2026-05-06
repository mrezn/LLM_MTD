from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate

from llm_mtd_eval.state.active_pool_state import build_active_pool_state
from llm_mtd_eval.state.normalizer import build_normalized_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_normalizer_builds_schema_valid_state() -> None:
    core_data = {
        "generated_at": "2026-04-21T12:00:00Z",
        "sensor_to_edge_latency_ms": [
            {
                "source": "edge2_gw",
                "role": "edge_gateway",
                "metric": "sensors.sen4.last_ingestion_latency_ms",
                "value": 12.4,
            }
        ],
        "edge_to_cloud_latency_ms": [
            {
                "source": "cloud_db",
                "role": "cloud_db",
                "metric": "last_edge_to_cloud_latency_ms",
                "value": 17.6,
            }
        ],
        "message_loss_counters": [
            {"source": "sen4", "role": "sensor", "metric": "generated_total", "value": 10},
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "sensors.sen4.received", "value": 10},
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "sensors.sen4.dropped", "value": 0},
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "sensors.sen4.queue_length", "value": 4},
        ],
        "throughput": [
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "received_bytes_per_second", "value": 123456.0}
        ],
        "attack_events": [
            {"source": "sen4_edge2_clouddb", "role": "attacker", "metric": "gateway_seen", "value": 1},
            {"source": "sen4_edge2_clouddb", "role": "attacker", "metric": "worker_seen", "value": 1},
            {"source": "sen4_edge2_clouddb", "role": "attacker", "metric": "cloud_seen", "value": 0},
            {"source": "sen4_edge2_clouddb", "role": "attacker", "metric": "attack_effect_success", "value": 0},
        ],
        "defense_events": [
            {"source": "sen4_edge2_clouddb", "role": "defender", "metric": "defense_success", "value": 0}
        ],
    }
    ryu_status = {"active_actions": {"a1": {"action": "rate_limit", "target": "sen4"}}}
    ryu_metrics_text = "\n".join(
        [
            "ryu_controller_active_policy_actions 1",
            "ryu_controller_flow_rules_installed_total 4",
            "ryu_controller_meters_added_total 1",
            "ryu_controller_last_action_duration_ms 22.0",
        ]
    )
    scenario = {
        "scenario_id": "sen4_edge2_clouddb",
        "entry_node": "sen4",
        "target_asset": "cloud_db",
        "mulval_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
    }
    mulval_policy = {
        "path_risk_scores": {
            "sen4->edge2_gw->edge2_vm_s4->cloud_db": 0.82
        }
    }
    state = build_normalized_state(
        core_data=core_data,
        ryu_status_data=ryu_status,
        ryu_metrics_text=ryu_metrics_text,
        scenario=scenario,
        mulval_policy=mulval_policy,
        active_pool_state=build_active_pool_state({"enabled": False}),
    )
    schema = json.loads(
        (PROJECT_ROOT / "configs" / "schemas" / "normalized_state.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validate(instance=state.model_dump(mode="json"), schema=schema)
    assert state.qos_context.queue_length == 4
    assert state.controller_context.flow_rules_installed == 4
    assert state.security_context.worker_seen is True


def test_normalizer_uses_experiment_summary_when_core_is_empty() -> None:
    experiment_summary = {
        "generated_at": "2026-04-23T07:47:51.254395+00:00",
        "sensor_to_edge_latency_ms": [
            {
                "source": "edge2_gw",
                "role": "edge_gateway",
                "metric": "sensors.sen4.last_ingestion_latency_ms",
                "value": 18.5,
            }
        ],
        "edge_to_cloud_latency_ms": [
            {
                "source": "cloud_db",
                "role": "cloud_db",
                "metric": "last_edge_to_cloud_latency_ms",
                "value": 4.25,
            }
        ],
        "message_loss_counters": [
            {"source": "sen4", "role": "sensor", "metric": "generated_total", "value": 20},
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "sensors.sen4.received", "value": 18},
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "sensors.sen4.dropped", "value": 2},
            {"source": "edge2_gw", "role": "edge_gateway", "metric": "sensors.sen4.queue_length", "value": 7},
        ],
        "throughput": [
            {
                "source": "edge2_gw",
                "role": "edge_gateway",
                "metric": "received_bytes_per_second",
                "value": 2048.0,
            }
        ],
    }
    scenario = {
        "scenario_id": "sen4_edge2_clouddb",
        "entry_node": "sen4",
        "target_asset": "cloud_db",
        "mulval_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
    }
    state = build_normalized_state(
        core_data={},
        experiment_summary=experiment_summary,
        ryu_status_data={},
        ryu_metrics_text="ryu_controller_active_policy_actions 2\nryu_controller_flow_rules_installed_total 5\n",
        scenario=scenario,
        mulval_policy={},
        active_pool_state=build_active_pool_state({"enabled": False}),
    )
    assert state.timestamp == "2026-04-23T07:47:51.254395+00:00"
    assert state.qos_context.sensor_to_gateway_latency_ms == 18.5
    assert state.qos_context.edge_to_cloud_latency_ms == 4.25
    assert state.qos_context.queue_length == 7
    assert state.qos_context.throughput_bps == 2048.0
    assert state.controller_context.active_policy_actions == 2
