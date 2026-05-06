from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from llm_mtd_eval.cli import build_arg_parser
from llm_mtd_eval.evaluators.run_stage import (
    _stage_validation,
    build_stage_outcome,
    compute_llm_baseline_alignment,
    reconcile_defense_effects,
    run_stage,
)
from llm_mtd_eval.llm_layer.defender_selector import select_baseline_top_defender


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _mock_model_config(tmp_path: Path) -> Path:
    source = PROJECT_ROOT / "configs" / "models" / "llm_only.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    config["llm"]["provider"] = "mock"
    config["llm"]["model_name"] = "mock-live-stage"
    path = tmp_path / "llm_stage_mock.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def test_cli_parses_run_stage_subcommand() -> None:
    args = build_arg_parser().parse_args(
        [
            "run-stage",
            "--model-config",
            "configs/models/llm_only.yaml",
            "--scenario-id",
            "sen4_edge2_clouddb",
            "--execute-attacker",
            "--execute-defender",
            "--observe-delay-seconds",
            "15",
            "--llm-timeout-seconds",
            "180",
            "--llm-max-retries",
            "1",
            "--llm-compact-prompt",
            "--llm-max-candidate-fields",
            "9",
        ]
    )
    assert args.command == "run-stage"
    assert args.execute_attacker is True
    assert args.execute_defender is True
    assert args.observe_delay_seconds == 15.0
    assert args.llm_timeout_seconds == 180.0
    assert args.llm_max_retries == 1
    assert args.llm_compact_prompt is True
    assert args.llm_max_candidate_fields == 9


def test_run_stage_mocked_live_path(monkeypatch, tmp_path: Path) -> None:
    model_config = _mock_model_config(tmp_path)

    class FakeStrategyManager:
        def __init__(self) -> None:
            self.parameters = {}

        @classmethod
        def from_file(cls, path):  # noqa: ANN001
            return cls()

        def active_lists(self, state, strict_preconditions=False):  # noqa: ANN001
            return {
                "attacker_ids": ["A2_sensor_http_abuse_sen4"],
                "defender_ids": ["D0_observe", "D1_quarantine_sen4"],
                "attackers": [
                    {
                        "id": "A2_sensor_http_abuse_sen4",
                        "name": "HTTP abuse from sen4",
                        "scenario_id": "sen4_edge2_clouddb",
                        "entry_node": "sen4",
                        "target_asset": "cloud_db",
                        "path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
                        "expected_effects": ["gateway_seen", "worker_seen", "cloud_seen"],
                        "live_attack_type": "sensor_to_edge_http_abuse",
                        "executor": {"name": "sensor_to_edge", "env": {"ENTRY_NODE": "sen4"}},
                        "base_cost": 0.4,
                        "base_reward": 0.8,
                    }
                ],
                "defenders": [
                    {
                        "id": "D0_observe",
                        "name": "Observe",
                        "scenario_id": "sen4_edge2_clouddb",
                        "action": "observe",
                        "target": "",
                        "action_payload": {"action": "observe"},
                        "expected_effects": [],
                        "base_cost": 0.0,
                        "base_reward": 0.1,
                    },
                    {
                        "id": "D1_quarantine_sen4",
                        "name": "Quarantine sen4",
                        "scenario_id": "sen4_edge2_clouddb",
                        "action": "quarantine_sensor",
                        "target": "sen4",
                        "action_payload": {"action": "quarantine_sensor", "target": "sen4"},
                        "expected_effects": ["drop_rules_active", "counters_stopped"],
                        "base_cost": 0.6,
                        "base_reward": 0.9,
                    },
                ],
            }

    class FakeStateBuilder:
        calls = 0

        @classmethod
        def build_state(cls, **kwargs):  # noqa: ANN003
            cls.calls += 1
            if cls.calls == 1:
                return {
                    "scenario_id": "sen4_edge2_clouddb",
                    "entry_node": "sen4",
                    "target_asset": "cloud_db",
                    "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
                    "path_stage": 2,
                    "path_stage_label": "worker",
                    "attack_active": True,
                    "attack_effect_success": False,
                    "defense_active": False,
                    "defense_success": False,
                    "mulval": {"current_path_risk": 0.82, "plausible_paths": [["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"]]},
                    "qos": {"sensor_to_edge_latency_ms": 3.0, "edge_to_cloud_latency_ms": 2.0, "loss_rate": 0.0},
                    "workload": {"throughput_bytes_per_second": 1024.0},
                    "overhead": {"controller_active_actions": 0, "flow_rules_installed": 0, "meters_added": 0},
                    "controller": {"active_actions": []},
                    "controller_reachable": True,
                    "defender_observation": {"attack_metrics": {"gateway_seen": 1}, "defense_metrics": {}},
                }
            return {
                "scenario_id": "sen4_edge2_clouddb",
                "entry_node": "sen4",
                "target_asset": "cloud_db",
                "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
                "path_stage": 1,
                "path_stage_label": "gateway",
                "attack_active": True,
                "attack_effect_success": False,
                "defense_active": True,
                "defense_success": True,
                "drop_rules_active": True,
                "counters_stopped": True,
                "mulval": {"current_path_risk": 0.82, "plausible_paths": [["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"]]},
                "qos": {"sensor_to_edge_latency_ms": 3.4, "edge_to_cloud_latency_ms": 2.1, "loss_rate": 0.01},
                "workload": {"throughput_bytes_per_second": 900.0},
                "overhead": {"controller_active_actions": 1, "flow_rules_installed": 10, "meters_added": 0},
                "controller": {"active_actions": [{"action": "quarantine_sensor", "target": "sen4"}]},
                "controller_reachable": True,
                "defender_observation": {"attack_metrics": {"gateway_seen": 1}, "defense_metrics": {"defense_success": 1}},
            }

    def fake_evolutionary_step(attackers, defenders, state, previous_population=None, parameters=None):  # noqa: ANN001
        return {
            "attacker_population_before": {"A2_sensor_http_abuse_sen4": 1.0},
            "attacker_population": {"A2_sensor_http_abuse_sen4": 1.0},
            "attacker_utilities": {"A2_sensor_http_abuse_sen4": 0.8},
            "attacker_average_utility": 0.8,
            "defender_population_before": {"D0_observe": 0.4, "D1_quarantine_sen4": 0.6},
            "defender_population": {"D0_observe": 0.25, "D1_quarantine_sen4": 0.75},
            "defender_utilities": {"D0_observe": 0.2, "D1_quarantine_sen4": 0.9},
            "defender_average_utility": 0.72,
        }

    def fake_select_strategy(strategies, population, utilities, mode="dominant", random_seed=None):  # noqa: ANN001
        chosen = strategies[0]
        strategy_id = chosen["id"]
        return {
            "id": strategy_id,
            "probability": population.get(strategy_id, 1.0),
            "utility": utilities.get(strategy_id, 0.0),
            "mode": mode,
            "strategy": chosen,
        }

    def fake_compact_selection(selection):  # noqa: ANN001
        if selection is None:
            return None
        strategy = selection["strategy"]
        return {
            "id": selection["id"],
            "name": strategy.get("name"),
            "probability": selection["probability"],
            "utility": selection["utility"],
            "mode": selection["mode"],
            "scenario_id": strategy.get("scenario_id"),
            "action": strategy.get("action") or strategy.get("live_attack_type"),
            "target": strategy.get("target") or strategy.get("target_asset"),
            "path": strategy.get("path"),
            "expected_effects": strategy.get("expected_effects", []),
            "action_payload": strategy.get("action_payload"),
        }

    def fake_execute_attacker(selection, state, execute, dispatch_url, cloud_logger_url, cloud_policy_url, timeout):  # noqa: ANN001
        return {
            "status": "dispatched",
            "plan": {"strategy_id": selection["id"], "path": selection["strategy"]["path"], "scenario_id": state["scenario_id"]},
            "post_result": {
                "ok": True,
                "status": 202,
                "body": '{"operation_id":"op-1"}',
                "json": {"operation_id": "op-1"},
                "url": dispatch_url or "http://127.0.0.1:9000/caldera/dispatch",
            },
        }

    def fake_action_payload_from_defender(selection):  # noqa: ANN001
        if not selection:
            return None
        payload = dict((selection.get("action_payload") or {}))
        return payload or {
            "action": selection.get("action", "observe"),
            "target": selection.get("target", ""),
        }

    def fake_cloud_policy_context_payload(state, selection_pair, attacker_execution=None):  # noqa: ANN001
        return {
            "scenario_id": state.get("scenario_id"),
            "state": state,
            "selected_attacker_strategy": selection_pair.get("attacker"),
            "selected_defender_strategy": selection_pair.get("defender"),
            "attacker_execution": attacker_execution,
        }

    def fake_endpoint_url(url, fallback_path):  # noqa: ANN001
        return url or f"http://127.0.0.1:9999{fallback_path}"

    def fake_post_json_with_container_fallback(url, payload, timeout, docker_container, docker_fallback, fallback_path):  # noqa: ANN001
        return {
            "ok": True,
            "status": 200,
            "body": "{}",
            "json": payload,
            "url": url,
        }

    def fake_post_json(url, payload, timeout=2.0):  # noqa: ANN001
        return {
            "ok": True,
            "status": 200,
            "body": '{"active_policy_actions":1,"flow_rules_installed":10,"meters_added":0,"status":"installed"}',
            "json": {
                "active_policy_actions": 1,
                "flow_rules_installed": 10,
                "meters_added": 0,
                "status": "installed",
            },
            "url": url,
            "payload": payload,
        }

    def fake_defense_action_confirmed(defender_execution, next_state):  # noqa: ANN001
        return (
            defender_execution.get("status") == "executed"
            and ((defender_execution.get("post_result") or {}).get("ok") is True)
            and bool((next_state or {}).get("defense_success"))
        )

    def fake_post_defense_result_event(cloud_logger_url, state, next_state, selection, execution, timeout):  # noqa: ANN001
        return {"status": "posted", "post_result": {"ok": True, "status": 200, "url": cloud_logger_url or "http://127.0.0.1:32854/attack/event"}}

    def fake_build_transition_record(previous_state, next_state, selection, execution, game):  # noqa: ANN001
        return {
            "schema_version": "llm-mtd-stage-transition-v2",
            "transition_id": "transition-1",
            "selection": {"attacker": {"id": "A2_sensor_http_abuse_sen4"}, "defender": {"id": "D1_quarantine_sen4"}},
            "previous_state": previous_state,
            "next_state": next_state,
            "execution": execution,
            "state_summary": {"scenario_id": previous_state["scenario_id"]},
        }

    def fake_build_decision_trace_record(previous_state, next_state, selection, execution, game, transition_id=""):  # noqa: ANN001
        return {
            "schema_version": "llm-mtd-decision-trace-v1",
            "transition_id": transition_id or "transition-1",
            "scenario_id": previous_state["scenario_id"],
            "selection": {"attacker": {"id": "A2_sensor_http_abuse_sen4"}, "defender": {"id": "D1_quarantine_sen4"}},
            "execution": {
                "attacker": {"status": "dispatched", "operation_id": "op-1"},
                "defender": {"status": "executed"},
            },
        }

    fake_modules = SimpleNamespace(
        strategy_manager=SimpleNamespace(StrategyManager=FakeStrategyManager),
        state_builder=SimpleNamespace(build_state=FakeStateBuilder.build_state),
        game_model=SimpleNamespace(evolutionary_step=fake_evolutionary_step),
        policy_selector=SimpleNamespace(select_strategy=fake_select_strategy, compact_selection=fake_compact_selection),
        strategy_runtime=SimpleNamespace(
            DEFAULT_ATTACKER_DISPATCH_URL="http://127.0.0.1:9000/caldera/dispatch",
            DEFAULT_CLOUD_POLICY_CONTAINER="mn.cloud_policy",
            DEFAULT_CLOUD_POLICY_DOCKER_FALLBACK=False,
            load_population=lambda path: {},  # noqa: ARG005
            save_population=lambda path, game, stage_result: path.write_text("{}", encoding="utf-8"),
            execute_attacker=fake_execute_attacker,
            action_payload_from_defender=fake_action_payload_from_defender,
            cloud_policy_context_payload=fake_cloud_policy_context_payload,
            endpoint_url=fake_endpoint_url,
            post_json_with_container_fallback=fake_post_json_with_container_fallback,
            post_json=fake_post_json,
            defense_action_confirmed=fake_defense_action_confirmed,
            post_defense_result_event=fake_post_defense_result_event,
            should_skip_persistence_for_state=lambda state: False,
        ),
        stage_transition=SimpleNamespace(
            build_transition_record=fake_build_transition_record,
            build_decision_trace_record=fake_build_decision_trace_record,
            append_transition=lambda path, record: record,
            append_decision_trace=lambda path, record: record,
        ),
    )

    monkeypatch.setattr(
        "llm_mtd_eval.evaluators.run_stage.load_emo_strategy_modules",
        lambda workspace_root: fake_modules,
    )
    monkeypatch.setattr(
        "llm_mtd_eval.evaluators.run_stage.strategy_dir",
        lambda workspace_root: tmp_path,
    )

    payload = run_stage(
        model_config_path=model_config,
        scenario_id="sen4_edge2_clouddb",
        execute_attacker=True,
        execute_defender=True,
        observe_delay_seconds=0.0,
        output_root=tmp_path,
    )

    result = payload["result"]
    artifacts = payload["artifacts"]
    assert result["decision_source"] == "llm_defender"
    assert result["selection"]["attacker"]["id"] == "A2_sensor_http_abuse_sen4"
    assert result["selection"]["defender"]["id"] == "D1_quarantine_sen4"
    assert result["execution"]["attacker"]["status"] == "dispatched"
    assert result["execution"]["defender"]["status"] == "executed"
    assert result["stage_summary"]["decision_source"] == "llm_defender"
    assert result["stage_validation"]["comparable_stage"] is True
    assert result["stage_validation"]["llm_stage_valid"] is False
    assert result["stage_validation"]["stage_kind"] == "llm_fallback"
    assert result["stage_validation"]["paper_valid_stage"] is False
    assert result["stage_validation"]["learning_valid_stage"] is False
    assert result["stage_validation"]["operationally_executed_debug_stage"] is True
    assert result["stage_validation"]["defense_effects_confirmed"] is True
    assert result["llm"]["baseline_top_defender_strategy_id"] == "D1_quarantine_sen4"
    assert result["llm"]["llm_selected_defender_strategy_id"] == "D0_observe"
    assert result["llm"]["final_defender_strategy_id"] == "D1_quarantine_sen4"
    assert result["llm"]["executed_via_fallback"] is True
    assert result["llm"]["fallback_reason"] == "observe_disallowed_high_urgency"
    assert result["llm"]["raw_llm_reasoning_summary"] == "No state was provided, so observe is the safe fallback."
    assert "was disallowed" in result["llm"]["executed_decision_reasoning_summary"]
    assert result["fallback_resolution"]["raw_llm_selected_defender_strategy_id"] == "D0_observe"
    assert result["fallback_resolution"]["final_defender_strategy_id"] == "D1_quarantine_sen4"
    assert isinstance(result["llm"]["llm_ranked_candidates"], list)
    assert result["transition"]["baseline_top_defender_strategy_id"] == "D1_quarantine_sen4"
    assert result["transition"]["raw_llm_selected_defender_strategy_id"] == "D0_observe"
    assert result["decision_trace"]["final_defender_strategy_id"] == "D1_quarantine_sen4"
    assert result["decision_trace"]["llm_baseline_alignment"] == "overrode"
    assert result["decision_trace"]["final_baseline_alignment"] == "followed"
    context_json = result["execution"]["defender"]["cloud_policy_context"]["json"]
    assert context_json["policy_mode"] == "llm_direct_ryu"
    assert context_json["cloud_policy_observe_only_normalized"] is False
    assert result["persistence"]["population_saved"] is False
    assert result["persistence"]["learning_eligible"] is False
    assert Path(artifacts["result_path"]).exists()
    assert Path(artifacts["defender_trace_path"]).exists()


def test_stage_validation_marks_dry_run_stage_as_nonlearning() -> None:
    defender_result = SimpleNamespace(
        request_success=True,
        fallback_used=False,
        parse_success=True,
        recovery_used=False,
        request_error="",
        selection={
            "strategy": {"expected_effects": ["drop_rules_active", "counters_stopped"]},
            "expected_effects": ["drop_rules_active", "counters_stopped"],
        },
    )
    modules = SimpleNamespace(
        strategy_runtime=SimpleNamespace(
            defense_action_confirmed=lambda defender_execution, next_state: False,
        )
    )

    validation = _stage_validation(
        state={"attack_active": True, "path_stage": 2},
        next_state={"attack_active": True, "path_stage": 3, "attack_effect_success": True, "defense_success": False},
        attacker_execution={"status": "dispatched"},
        defender_execution={"status": "dry_run", "payload": {"action": "quarantine_sensor", "target": "sen4"}},
        defender_result=defender_result,
        modules=modules,
    )

    assert validation["comparable_stage"] is True
    assert validation["stage_kind"] == "dry_run"
    assert validation["llm_stage_valid"] is False
    assert validation["paper_valid_stage"] is False
    assert validation["learning_valid_stage"] is False
    assert validation["defense_confirmed"] is False
    assert validation["stage_success"] is False


def test_reconcile_defense_effects_uses_event_and_ryu_signals() -> None:
    result = reconcile_defense_effects(
        {"defender": {"expected_effects": ["drop_rules_active", "counters_stopped"]}},
        previous_state={"path_stage": 2, "overhead": {"controller_active_actions": 0}},
        next_state={
            "path_stage": 3,
            "drop_rules_active": False,
            "counters_stopped": False,
            "overhead": {"controller_active_actions": 1, "flow_rules_installed": 20, "meters_added": 0},
        },
        defender_execution={
            "status": "executed",
            "payload": {"action": "quarantine_sensor", "target": "sen4"},
            "post_result": {
                "ok": True,
                "json": {"active_policy_actions": 1, "flow_rules_installed": 20, "status": "installed"},
            },
            "defense_event": {
                "status": "posted",
                "payload": {
                    "signals": {
                        "drop_rules_active": True,
                        "counters_stopped": True,
                        "active_policy_actions": 1,
                        "flow_rules_installed": 20,
                    }
                },
            },
        },
    )

    assert result["defense_confirmed"] is True
    assert result["effects_confirmed"] is True
    assert result["observed_effects"] == ["drop_rules_active", "counters_stopped"]
    assert result["missing_effects"] == []
    assert result["effect_source_conflict"] is True


def test_semantic_effect_mapping_confirms_gateway_block_and_path_break() -> None:
    result = reconcile_defense_effects(
        {
            "defender": {
                "id": "D3_isolate_edge2_gw_for_sen4",
                "action": "isolate_sensor",
                "target": "edge2_gw",
                "path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
                "expected_effects": ["gateway_blocked", "path_broken"],
            }
        },
        previous_state={
            "path_stage": 3,
            "attack_active": True,
            "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        },
        next_state={
            "path_stage": 3,
            "attack_active": True,
            "attack_effect_success": True,
            "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
            "drop_rules_active": True,
            "counters_stopped": True,
            "overhead": {"controller_active_actions": 1, "flow_rules_installed": 20},
        },
        defender_execution={
            "status": "executed",
            "payload": {"action": "isolate_sensor", "target": "edge2_gw"},
            "post_result": {
                "ok": True,
                "json": {
                    "status": "installed",
                    "active_policy_actions": 1,
                    "flow_rules_installed": 20,
                    "drop_rules_active": True,
                    "counters_stopped": True,
                },
            },
            "defense_event": {
                "payload": {
                    "signals": {
                        "drop_rules_active": True,
                        "counters_stopped": True,
                        "active_policy_actions": 1,
                        "flow_rules_installed": 20,
                    }
                }
            },
        },
    )

    assert result["effects_confirmed"] is True
    assert result["semantic_observed_defense_effects"] == ["gateway_blocked", "path_broken"]
    assert result["semantic_missing_defense_effects"] == []
    assert result["missing_effects"] == []


def test_build_stage_outcome_keeps_attack_effect_consistent() -> None:
    defender_result = SimpleNamespace(
        request_success=True,
        fallback_used=False,
        parse_success=True,
        recovery_used=False,
        request_error="",
        selection={
            "strategy": {"expected_effects": ["drop_rules_active"]},
            "expected_effects": ["drop_rules_active"],
        },
    )

    outcome = build_stage_outcome(
        state={"attack_active": True, "path_stage": 2},
        next_state={
            "scenario_id": "sen4_edge2_clouddb",
            "attack_active": True,
            "path_stage": 3,
            "attack_effect_success": True,
            "defense_success": False,
            "drop_rules_active": True,
            "overhead": {"controller_active_actions": 1, "flow_rules_installed": 20},
        },
        attacker_execution={"status": "dispatched"},
        defender_execution={
            "status": "executed",
            "payload": {"action": "quarantine_sensor", "target": "sen4"},
            "post_result": {"ok": True, "json": {"active_policy_actions": 1, "flow_rules_installed": 20}},
        },
        defender_result=defender_result,
    )

    assert outcome["security_outcome"]["attack_effect_success"] is True
    assert outcome["state_summary"]["attack_effect_success"] is True
    assert outcome["stage_validation"]["defense_applied_but_not_effective"] is True
    assert outcome["stage_validation"]["stage_success"] is False


def test_compute_llm_baseline_alignment_marks_override_from_raw_choice() -> None:
    alignment = compute_llm_baseline_alignment(
        baseline_top_defender_strategy_id="D0_observe",
        raw_llm_selected_defender_strategy_id="D1_quarantine_sen4",
        final_defender_strategy_id="D1_quarantine_sen4",
        executed_via_fallback=False,
    )

    assert alignment["llm_baseline_alignment"] == "overrode"
    assert alignment["raw_llm_alignment_vs_baseline"] == "overrode"
    assert alignment["final_decision_alignment_vs_baseline"] == "overrode"


def test_compute_llm_baseline_alignment_handles_fallback_final_choice() -> None:
    alignment = compute_llm_baseline_alignment(
        baseline_top_defender_strategy_id="D0_observe",
        raw_llm_selected_defender_strategy_id="D0_observe",
        final_defender_strategy_id="D4_isolate_edge2_vm_s4",
        executed_via_fallback=True,
    )

    assert alignment["raw_llm_alignment_vs_baseline"] == "followed"
    assert alignment["final_decision_alignment_vs_baseline"] == "overrode"
    assert alignment["llm_baseline_alignment"] == "followed"


def test_outcome_consistency_marks_confirmed_but_ineffective_defense() -> None:
    defender_result = SimpleNamespace(
        request_success=True,
        fallback_used=False,
        parse_success=True,
        recovery_used=False,
        request_error="",
        selection={
            "id": "D3_isolate_edge2_gw_for_sen4",
            "action": "isolate_sensor",
            "target": "edge2_gw",
            "path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
            "expected_effects": ["gateway_blocked", "path_broken"],
        },
    )

    outcome = build_stage_outcome(
        state={"attack_active": True, "path_stage": 3, "current_path": ["sen4", "edge2_gw", "cloud_db"]},
        next_state={
            "attack_active": True,
            "path_stage": 3,
            "attack_effect_success": True,
            "defense_success": True,
            "drop_rules_active": True,
            "counters_stopped": True,
            "current_path": ["sen4", "edge2_gw", "cloud_db"],
            "overhead": {"controller_active_actions": 1, "flow_rules_installed": 20},
        },
        attacker_execution={"status": "dispatched"},
        defender_execution={
            "status": "executed",
            "payload": {"action": "isolate_sensor", "target": "edge2_gw"},
            "post_result": {"ok": True, "json": {"status": "installed", "active_policy_actions": 1, "flow_rules_installed": 20}},
        },
        defender_result=defender_result,
    )

    validation = outcome["stage_validation"]
    assert validation["defense_effects_confirmed"] is True
    assert outcome["security_outcome"]["attack_effect_success"] is True
    assert outcome["security_outcome"]["defense_success"] is False
    assert validation["defense_applied_but_not_effective"] is True
    assert validation["security_stage_success"] is False
    assert validation["stage_success"] is False


def test_build_stage_outcome_merges_confirmed_effects_into_next_state() -> None:
    defender_result = SimpleNamespace(
        request_success=True,
        fallback_used=False,
        parse_success=True,
        recovery_used=False,
        request_error="",
        selection={
            "id": "D1_quarantine_sen4",
            "action": "quarantine_sensor",
            "target": "sen4",
            "expected_effects": ["drop_rules_active", "counters_stopped"],
        },
    )
    next_state = {
        "scenario_id": "sen4_edge2_clouddb",
        "attack_active": True,
        "path_stage": 3,
        "attack_effect_success": True,
        "drop_rules_active": False,
        "counters_stopped": False,
        "overhead": {"controller_active_actions": 1, "flow_rules_installed": 20},
    }

    outcome = build_stage_outcome(
        state={"attack_active": True, "path_stage": 2},
        next_state=next_state,
        attacker_execution={"status": "dispatched"},
        defender_execution={
            "status": "executed",
            "payload": {"action": "quarantine_sensor", "target": "sen4"},
            "post_result": {"ok": True, "json": {"status": "installed", "active_policy_actions": 1, "flow_rules_installed": 20}},
            "defense_event": {
                "payload": {
                    "signals": {
                        "drop_rules_active": True,
                        "counters_stopped": True,
                        "active_policy_actions": 1,
                        "flow_rules_installed": 20,
                    }
                }
            },
        },
        defender_result=defender_result,
    )

    assert next_state["raw_drop_rules_active"] is False
    assert next_state["normalized_drop_rules_active"] is True
    assert next_state["drop_rules_active"] is True
    assert next_state["raw_counters_stopped"] is False
    assert next_state["normalized_counters_stopped"] is True
    assert next_state["counters_stopped"] is True
    assert outcome["state_summary"]["drop_rules_active"] is True
    assert outcome["state_summary"]["raw_drop_rules_active"] is False
    assert outcome["stage_validation"]["effect_resolution_policy"] == "semantic_confirmation_preferred_over_stale_raw_state"


def test_select_baseline_top_defender_uses_deterministic_tiebreaks() -> None:
    defenders = [
        {"id": "D2", "base_cost": 0.3},
        {"id": "D1", "base_cost": 0.2},
        {"id": "D3", "base_cost": 0.9},
    ]
    game = {
        "defender_utilities": {"D1": 0.0, "D2": 0.0, "D3": 0.0},
        "defender_population": {"D1": 0.4, "D2": 0.4, "D3": 0.2},
    }

    chosen = select_baseline_top_defender(defenders, game)

    assert chosen["strategy_id"] == "D1"
    assert chosen["population_share"] == 0.4
    assert chosen["base_cost"] == 0.2
    assert chosen["tiebreak_reason"] == "utility_population_tie_lowest_base_cost"


def test_stage_outcome_timeout_failure_is_not_paper_or_learning_valid() -> None:
    defender_result = SimpleNamespace(
        request_success=False,
        fallback_used=True,
        parse_success=False,
        recovery_used=False,
        request_error="RuntimeError:Ollama completion failed: timed out",
        selection={"strategy": {"expected_effects": ["drop_rules_active"]}, "expected_effects": ["drop_rules_active"]},
    )

    outcome = build_stage_outcome(
        state={"attack_active": True, "path_stage": 2},
        next_state={"attack_active": True, "path_stage": 3, "attack_effect_success": False, "defense_success": True, "drop_rules_active": True},
        attacker_execution={"status": "dispatched"},
        defender_execution={"status": "executed", "payload": {"action": "quarantine_sensor"}, "post_result": {"ok": True, "json": {"active_policy_actions": 1, "flow_rules_installed": 20}}},
        defender_result=defender_result,
    )

    validation = outcome["stage_validation"]
    assert validation["paper_valid_stage"] is False
    assert validation["learning_valid_stage"] is False
    assert "llm_request_failed" in validation["invalidity_reasons"]
