from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from llm_mtd_eval.reports.build_figures import build_figures
from llm_mtd_eval.reports.build_tables import write_tables
from llm_mtd_eval.reports.load_results import load_result_frames
from llm_mtd_eval.reports.metric_mapping import formal_mapping_frame
from llm_mtd_eval.reports.report_cli import build_report


def test_load_result_frames_normalizes_stage_logs(tmp_path: Path) -> None:
    outputs_dir = tmp_path / "outputs"
    raw_dir = outputs_dir / "raw"
    trace_dir = outputs_dir / "traces"
    raw_dir.mkdir(parents=True)
    trace_dir.mkdir(parents=True)

    stage_history_path = raw_dir / "live_stage_history.jsonl"
    decision_trace_path = raw_dir / "live_decision_trace.jsonl"
    stage_summaries_path = raw_dir / "stage_summaries.jsonl"
    population_path = raw_dir / "live_population_state.json"

    stage_row = _sample_stage_row()
    decision_row = _sample_decision_row()
    summary_row = _sample_summary_row()
    population_payload = {
        "attacker": {"A1": 0.6},
        "defender": {"D1": 0.7},
        "last_stage": {
            "scenario_id": "scenario1",
            "path_stage": 2,
            "selected_attacker": "A1",
            "selected_defender": "D1",
        },
    }

    _write_jsonl(stage_history_path, [stage_row])
    _write_jsonl(decision_trace_path, [decision_row])
    _write_jsonl(stage_summaries_path, [summary_row])
    population_path.write_text(json.dumps(population_payload), encoding="utf-8")

    raw_stage_path = raw_dir / "stage_run123.json"
    raw_stage_path.write_text(
        json.dumps(
            {
                "run_id": "stage_run123",
                "transition": {"stage_id": 1, "stage_summary": {"scenario_id": "scenario1"}},
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "stage_run123_defender_trace.json").write_text(
        json.dumps(
            {
                "provider": "ollama",
                "model_name": "gemma4:e4b",
                "latency_ms": 123.0,
                "retries_used": 1,
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "stage_run123_summary_trace.json").write_text(
        json.dumps({"latency_ms": 32.0}),
        encoding="utf-8",
    )

    frames = load_result_frames(
        method="llm_defender",
        stage_history_path=stage_history_path,
        decision_trace_path=decision_trace_path,
        stage_summaries_path=stage_summaries_path,
        population_path=population_path,
    )

    assert len(frames.stage_df) == 1
    row = frames.stage_df.iloc[0]
    assert row["summary_text"] == "Line one. Line two."
    assert row["llm_latency_ms"] == 123.0
    assert row["sensor_to_edge_latency_delta_ms"] == 1.5
    assert row["throughput_delta_bps"] == -20.0
    assert bool(row["llm_parse_success"]) is True
    assert bool(row["drop_rules_active"]) is True
    assert bool(row["raw_drop_rules_active"]) is False
    assert row["llm_baseline_alignment"] == "overrode"
    assert len(frames.population_evolution_df) == 2
    assert set(frames.population_evolution_df["role"]) == {"attacker", "defender"}


def test_formal_mapping_frame_contains_required_terms() -> None:
    frame = formal_mapping_frame()
    required_terms = {
        "SAL",
        "SAP",
        "AC",
        "DC",
        "resource_significance",
        "impact_weight",
        "controller_overhead",
        "qos_degradation",
        "defense_success",
    }
    assert required_terms.issubset(set(frame["formal_term"]))


def test_report_table_and_figure_smoke(tmp_path: Path) -> None:
    emo_root = _build_fake_emo_root(tmp_path / "LLM_MTD_emo")
    frames = load_result_frames(
        method="llm_defender",
        stage_history_path=_write_stage_bundle(tmp_path / "bundle"),
        decision_trace_path=(tmp_path / "bundle" / "outputs" / "raw" / "live_decision_trace.jsonl"),
        stage_summaries_path=(tmp_path / "bundle" / "outputs" / "raw" / "stage_summaries.jsonl"),
        population_path=(tmp_path / "bundle" / "outputs" / "raw" / "live_population_state.json"),
    )
    report_dir = tmp_path / "reports"
    table_paths = write_tables(
        output_dir=report_dir,
        emo_root=emo_root,
        eval_stage_df=frames.stage_df,
        combined_stage_df=frames.stage_df,
        eval_decision_df=frames.decision_df,
        scenario_filter=["scenario1"],
    )
    figure_paths = build_figures(
        stage_df=frames.stage_df,
        population_evolution_df=frames.population_evolution_df,
        output_dir=report_dir,
        figure_format="png",
        paper_mode=True,
        scenario_filter=["scenario1"],
    )

    assert table_paths["baseline_vs_llm_summary"].exists()
    assert table_paths["stage_case_study"].exists()
    assert table_paths["llm_vs_baseline_decision_alignment"].exists()
    assert table_paths["llm_candidate_ranking_case_study"].exists()
    assert figure_paths["attack_defense_outcomes_by_method"].exists()
    assert figure_paths["llm_baseline_alignment"].exists()
    assert figure_paths["llm_candidate_tradeoff_scatter"].exists()
    assert figure_paths["llm_decision_timing_vs_path_stage"].exists()
    assert figure_paths["stage_trace_case_study"].exists()
    for figure_path in figure_paths.values():
        assert figure_path.with_suffix(".csv").exists()


def test_stage_case_study_normalizes_observe_success_and_attacker_fallback(tmp_path: Path) -> None:
    emo_root = _build_fake_emo_root(tmp_path / "LLM_MTD_emo_case")
    bundle_root = tmp_path / "bundle_case"
    stage_history_path = _write_stage_bundle(bundle_root)
    decision_trace_path = bundle_root / "outputs" / "raw" / "live_decision_trace.jsonl"
    stage_summaries_path = bundle_root / "outputs" / "raw" / "stage_summaries.jsonl"
    population_path = bundle_root / "outputs" / "raw" / "live_population_state.json"

    stage_row = _sample_stage_row()
    stage_row["selection"]["attacker"] = {}
    stage_row["state_summary"]["defense_success"] = True
    stage_row["stage_summary"]["security_outcome"]["defense_success"] = True
    stage_row["defender_action"] = "observe"
    stage_row["selection"]["defender"]["action"] = "observe"
    stage_row["execution_summary"]["defender"]["action"] = "observe"
    stage_row["execution_summary"]["defender"]["status"] = "observe_only"
    stage_row["execution"]["defender"]["status"] = "observe_only"
    stage_row["stage_validation"]["defense_confirmed"] = False
    stage_row["stage_summary"]["execution"]["defender_status"] = "observe_only"
    stage_row["stage_summary"]["execution"]["defense_confirmed"] = False
    stage_row["llm_ranked_candidates"] = [{"strategy_id": "D0_observe", "baseline_utility_prior": 0.1, "expected_security_gain": 0.1, "expected_qos_impact": 0.0, "expected_controller_cost": 0.0}]
    _write_jsonl(stage_history_path, [stage_row])

    frames = load_result_frames(
        method="llm_defender",
        stage_history_path=stage_history_path,
        decision_trace_path=decision_trace_path,
        stage_summaries_path=stage_summaries_path,
        population_path=population_path,
    )
    report_dir = tmp_path / "reports_case"
    table_paths = write_tables(
        output_dir=report_dir,
        emo_root=emo_root,
        eval_stage_df=frames.stage_df,
        combined_stage_df=frames.stage_df,
        eval_decision_df=frames.decision_df,
        scenario_filter=["scenario1"],
    )
    stage_case = json.loads(
        pd.read_csv(table_paths["stage_case_study"]).to_json(orient="records")
    )[0]
    assert stage_case["attacker_strategy_id"] == "A1"
    assert stage_case["defender_executed"] == "observe_only"
    assert stage_case["defense_success"] is False
    assert "defense_effects_confirmed" in stage_case
    assert "defense_applied_but_not_effective" in stage_case
    scenario_setup = pd.read_csv(table_paths["scenario_attack_setup"])
    assert "quarantine_sensor" in scenario_setup.loc[0, "candidate_defender_actions"]
    assert "rate_limit" in scenario_setup.loc[0, "candidate_defender_actions"]


def test_alignment_table_uses_canonical_baseline_top_fields(tmp_path: Path) -> None:
    emo_root = _build_fake_emo_root(tmp_path / "LLM_MTD_emo_alignment")
    bundle_root = tmp_path / "bundle_alignment"
    stage_history_path = _write_stage_bundle(bundle_root)
    decision_trace_path = bundle_root / "outputs" / "raw" / "live_decision_trace.jsonl"
    stage_summaries_path = bundle_root / "outputs" / "raw" / "stage_summaries.jsonl"
    population_path = bundle_root / "outputs" / "raw" / "live_population_state.json"

    stage_row = _sample_stage_row()
    stage_row["baseline_top_defender_strategy_id"] = "D3"
    stage_row["baseline_top_defender_population_share"] = 0.44
    stage_row["baseline_top_defender_tiebreak_reason"] = "utility_tie_highest_population_share"
    stage_row["llm_selected_defender_strategy_id"] = "D3"
    stage_row["raw_llm_selected_defender_strategy_id"] = "D3"
    stage_row["final_defender_strategy_id"] = "D3"
    stage_row["llm_baseline_alignment"] = "overrode"
    stage_row["stage_validation"]["defense_executed"] = True
    stage_row["stage_validation"]["defense_effects_confirmed"] = True
    stage_row["stage_validation"]["defense_applied_but_not_effective"] = True
    _write_jsonl(stage_history_path, [stage_row])

    frames = load_result_frames(
        method="llm_defender",
        stage_history_path=stage_history_path,
        decision_trace_path=decision_trace_path,
        stage_summaries_path=stage_summaries_path,
        population_path=population_path,
    )
    table_paths = write_tables(
        output_dir=tmp_path / "reports_alignment",
        emo_root=emo_root,
        eval_stage_df=frames.stage_df,
        combined_stage_df=frames.stage_df,
        eval_decision_df=frames.decision_df,
        scenario_filter=["scenario1"],
    )

    alignment = pd.read_csv(table_paths["llm_vs_baseline_decision_alignment"]).iloc[0]
    assert alignment["baseline_top_defender"] == "D3"
    assert bool(alignment["aligned"]) is True
    assert alignment["raw_llm_alignment"] == "followed"
    assert alignment["final_executed_alignment"] == "followed"
    assert alignment["baseline_top_defender_tiebreak_reason"] == "utility_tie_highest_population_share"
    assert float(alignment["baseline_top_defender_population_share"]) == 0.44


def test_alignment_loader_prefers_ids_over_stale_stored_alignment(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle_alignment_loader"
    stage_history_path = _write_stage_bundle(bundle_root)
    decision_trace_path = bundle_root / "outputs" / "raw" / "live_decision_trace.jsonl"
    stage_summaries_path = bundle_root / "outputs" / "raw" / "stage_summaries.jsonl"
    population_path = bundle_root / "outputs" / "raw" / "live_population_state.json"

    stage_row = _sample_stage_row()
    stage_row["baseline_top_defender_strategy_id"] = "D0_observe"
    stage_row["raw_llm_selected_defender_strategy_id"] = "D1_quarantine_sen4"
    stage_row["final_defender_strategy_id"] = "D1_quarantine_sen4"
    stage_row["llm_baseline_alignment"] = "followed"
    stage_row["llm"] = {
        **(stage_row.get("llm") or {}),
        "raw_llm_selected_defender_strategy_id": "D1_quarantine_sen4",
        "final_defender_strategy_id": "D1_quarantine_sen4",
        "llm_baseline_alignment": "followed",
    }
    _write_jsonl(stage_history_path, [stage_row])

    frames = load_result_frames(
        method="llm_defender",
        stage_history_path=stage_history_path,
        decision_trace_path=decision_trace_path,
        stage_summaries_path=stage_summaries_path,
        population_path=population_path,
    )

    row = frames.stage_df.iloc[0]
    assert row["llm_baseline_alignment"] == "overrode"
    assert row["raw_llm_alignment"] == "overrode"
    assert row["final_executed_alignment"] == "overrode"


def test_fallback_stage_rows_preserve_raw_and_final_defenders(tmp_path: Path) -> None:
    emo_root = _build_fake_emo_root(tmp_path / "LLM_MTD_emo_fallback")
    bundle_root = tmp_path / "bundle_fallback"
    stage_history_path = _write_stage_bundle(bundle_root)
    decision_trace_path = bundle_root / "outputs" / "raw" / "live_decision_trace.jsonl"
    stage_summaries_path = bundle_root / "outputs" / "raw" / "stage_summaries.jsonl"
    population_path = bundle_root / "outputs" / "raw" / "live_population_state.json"

    stage_row = _sample_stage_row()
    stage_row["llm"]["fallback_used"] = True
    stage_row["llm"]["raw_llm_selected_defender_strategy_id"] = "D0_observe"
    stage_row["llm"]["final_defender_strategy_id"] = "D4_isolate_edge2_vm_s4"
    stage_row["llm"]["executed_via_fallback"] = True
    stage_row["llm"]["fallback_reason"] = "observe_disallowed_high_urgency"
    stage_row["llm"]["raw_llm_reasoning_summary"] = "Observe for now."
    stage_row["llm"]["executed_decision_reasoning_summary"] = "Observe was disallowed, so D4_isolate_edge2_vm_s4 was executed."
    stage_row["llm_selected_defender_strategy_id"] = "D0_observe"
    stage_row["final_defender_strategy_id"] = "D4_isolate_edge2_vm_s4"
    stage_row["executed_via_fallback"] = True
    stage_row["fallback_reason"] = "observe_disallowed_high_urgency"
    _write_jsonl(stage_history_path, [stage_row])

    frames = load_result_frames(
        method="llm_defender",
        stage_history_path=stage_history_path,
        decision_trace_path=decision_trace_path,
        stage_summaries_path=stage_summaries_path,
        population_path=population_path,
    )
    table_paths = write_tables(
        output_dir=tmp_path / "reports_fallback",
        emo_root=emo_root,
        eval_stage_df=frames.stage_df,
        combined_stage_df=frames.stage_df,
        eval_decision_df=frames.decision_df,
        scenario_filter=["scenario1"],
    )

    case_row = pd.read_csv(table_paths["stage_case_study"]).iloc[0]
    assert case_row["raw_llm_selected_defender_strategy_id"] == "D0_observe"
    assert case_row["final_defender_strategy_id"] == "D4_isolate_edge2_vm_s4"
    assert bool(case_row["executed_via_fallback"]) is True

    alignment_row = pd.read_csv(table_paths["llm_vs_baseline_decision_alignment"]).iloc[0]
    assert alignment_row["llm_selected_defender"] == "D0_observe"
    assert alignment_row["final_defender_strategy_id"] == "D4_isolate_edge2_vm_s4"


def test_build_report_excludes_non_paper_valid_eval_stages_by_default(tmp_path: Path) -> None:
    emo_root = _build_fake_emo_root(tmp_path / "LLM_MTD_emo_report")
    bundle_root = tmp_path / "bundle_report"
    stage_history_path = _write_stage_bundle(bundle_root)
    raw_dir = bundle_root / "outputs" / "raw"
    invalid_row = _sample_stage_row()
    invalid_row["stage_id"] = 2
    invalid_row["stage_validation"]["paper_valid_stage"] = False
    invalid_row["stage_validation"]["llm_stage_valid"] = False
    invalid_row["stage_validation"]["invalidity_reasons"] = ["llm_request_failed"]
    invalid_row["llm"]["request_success"] = False
    invalid_row["llm"]["request_error"] = "RuntimeError:Ollama completion failed: timed out"
    _write_jsonl(stage_history_path, [_sample_stage_row(), invalid_row])

    manifest = build_report(
        eval_stage_history=stage_history_path,
        eval_decision_trace=raw_dir / "live_decision_trace.jsonl",
        eval_stage_summaries=raw_dir / "stage_summaries.jsonl",
        eval_population=raw_dir / "live_population_state.json",
        baseline_stage_history=emo_root / "integrations" / "strategy" / "stage_history.jsonl",
        baseline_decision_trace=emo_root / "integrations" / "strategy" / "decision_trace.jsonl",
        baseline_population=emo_root / "integrations" / "strategy" / "population_state.json",
        output_dir=tmp_path / "reports_filter",
        scenario_filter=["scenario1"],
        paper_mode=True,
    )

    assert manifest["row_counts"]["eval_stage_rows"] == 2
    assert manifest["row_counts"]["eval_report_stage_rows"] == 1
    validity = pd.read_csv(manifest["tables"]["stage_validity_summary"])
    llm_row = validity.loc[validity["method"].eq("LLM defender")].iloc[0]
    assert int(llm_row["total_stages"]) == 2
    assert int(llm_row["paper_valid_stages"]) == 1
    assert int(llm_row["timeout_failed_stages"]) == 1


def test_build_report_manifest_includes_figure_csv_paths(tmp_path: Path) -> None:
    emo_root = _build_fake_emo_root(tmp_path / "LLM_MTD_emo_manifest")
    bundle_root = tmp_path / "bundle_manifest"
    stage_history_path = _write_stage_bundle(bundle_root)
    raw_dir = bundle_root / "outputs" / "raw"

    manifest = build_report(
        eval_stage_history=stage_history_path,
        eval_decision_trace=raw_dir / "live_decision_trace.jsonl",
        eval_stage_summaries=raw_dir / "stage_summaries.jsonl",
        eval_population=raw_dir / "live_population_state.json",
        baseline_stage_history=emo_root / "integrations" / "strategy" / "stage_history.jsonl",
        baseline_decision_trace=emo_root / "integrations" / "strategy" / "decision_trace.jsonl",
        baseline_population=emo_root / "integrations" / "strategy" / "population_state.json",
        output_dir=tmp_path / "reports_manifest",
        scenario_filter=["scenario1"],
        paper_mode=True,
    )

    assert "figure_data" in manifest
    csv_path = Path(manifest["figure_data"]["attack_defense_outcomes_by_method"])
    assert csv_path.exists()
    assert csv_path.suffix == ".csv"


def _write_stage_bundle(root: Path) -> Path:
    outputs_dir = root / "outputs"
    raw_dir = outputs_dir / "raw"
    trace_dir = outputs_dir / "traces"
    raw_dir.mkdir(parents=True)
    trace_dir.mkdir(parents=True)
    stage_history_path = raw_dir / "live_stage_history.jsonl"
    decision_trace_path = raw_dir / "live_decision_trace.jsonl"
    stage_summaries_path = raw_dir / "stage_summaries.jsonl"
    population_path = raw_dir / "live_population_state.json"

    _write_jsonl(stage_history_path, [_sample_stage_row()])
    _write_jsonl(decision_trace_path, [_sample_decision_row()])
    _write_jsonl(stage_summaries_path, [_sample_summary_row()])
    population_path.write_text(
        json.dumps(
            {
                "attacker": {"A1": 0.6},
                "defender": {"D1": 0.7},
                "last_stage": {
                    "scenario_id": "scenario1",
                    "path_stage": 2,
                    "selected_attacker": "A1",
                    "selected_defender": "D1",
                },
            }
        ),
        encoding="utf-8",
    )
    (raw_dir / "stage_run123.json").write_text(
        json.dumps(
            {
                "run_id": "stage_run123",
                "transition": {"stage_id": 1, "stage_summary": {"scenario_id": "scenario1"}},
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "stage_run123_defender_trace.json").write_text(
        json.dumps(
            {
                "provider": "ollama",
                "model_name": "gemma4:e4b",
                "latency_ms": 123.0,
                "retries_used": 1,
            }
        ),
        encoding="utf-8",
    )
    return stage_history_path


def _build_fake_emo_root(path: Path) -> Path:
    (path / "integrations" / "strategy").mkdir(parents=True)
    (path / "integrations").mkdir(exist_ok=True)
    (path / "network_model.py").write_text(
        "\n".join(
            [
                "CONTROLLER_NAME = 'c0'",
                "CONTROLLER_IP = '127.0.0.1'",
                "CONTROLLER_PORT = 6653",
                "SENSOR_NODE_MAP = {'sen4': ('s_edge2',), 'sen5': ('s_edge2',)}",
                "EDGE_NODE_MAP = {'edge2_gw': ('s_edge2',), 'edge2_vm_s4': ('s_edge2',)}",
                "CLOUD_NODE_MAP = {'cloud_db': ('s_cloud',), 'cloud_metrics': ('s_cloud',)}",
                "RESOURCE_PROFILES = {",
                "    'sensor_tiny': {'cpu': 0.2, 'memory': '128m'},",
                "    'edge_gateway_constrained': {'cpu': 1.0, 'memory': '512m'},",
                "    'edge_worker_constrained': {'cpu': 0.5, 'memory': '256m'},",
                "    'cloud_standard': {'cpu': 1.0, 'memory': '512m'},",
                "    'cloud_heavy': {'cpu': 2.0, 'memory': '1g'},",
                "}",
                "NODE_RESOURCE_PROFILE = {",
                "    'sen4': 'sensor_tiny',",
                "    'sen5': 'sensor_tiny',",
                "    'edge2_gw': 'edge_gateway_constrained',",
                "    'edge2_vm_s4': 'edge_worker_constrained',",
                "    'cloud_db': 'cloud_heavy',",
                "    'cloud_metrics': 'cloud_standard',",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    (path / "integrations" / "attack_scenarios.json").write_text(
        json.dumps(
            [
                {
                    "scenario_id": "scenario1",
                    "entry_node": "sen4",
                    "mulval_path": ["sen4", "edge2_gw", "cloud_db"],
                    "target_asset": "cloud_db",
                    "live_attack_type": "sensor_to_edge_http_abuse",
                    "success_criteria": {
                        "gateway_seen": True,
                        "worker_requests_increase": True,
                        "cloud_summary_rate_changes": True,
                    },
                    "candidate_defender_actions": [
                        {"action": "quarantine_sensor", "target": "sen4"},
                        {"action": "rate_limit", "target": "sen4", "kbps": 128},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    (path / "integrations" / "strategy" / "strategy_space.json").write_text(
        json.dumps(
            {
                "attacker_strategies": [],
                "defender_strategies": [
                    {"id": "D0_observe", "scenario_id": "*", "action": "observe"},
                    {"id": "D1_quarantine_sen4", "scenario_id": "scenario1", "action": "quarantine_sensor"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _sample_stage_row() -> dict[str, object]:
    return {
        "stage_id": 1,
        "recorded_at": "2026-04-30T00:00:00Z",
        "decision_source": "llm_defender",
        "selection": {
            "attacker": {"id": "A1", "name": "Attack", "action": "probe", "scenario_id": "scenario1"},
            "defender": {
                "id": "D1",
                "name": "Quarantine sen4",
                "action": "quarantine_sensor",
                "target": "sen4",
                "scenario_id": "scenario1",
            },
        },
        "execution": {
            "attacker": {"status": "dispatched"},
            "defender": {
                "status": "executed",
                "post_result": {
                    "body": json.dumps(
                        {
                            "active_policy_actions": 1,
                            "flow_rules_installed": 20,
                            "meters_added": 0,
                            "ryu_apply_duration_ms": 4.2,
                        }
                    )
                },
            },
        },
        "execution_summary": {
            "defender": {
                "status": "executed",
                "action": "quarantine_sensor",
                "target": "sen4",
                "ryu_response": {
                    "active_policy_actions": 1,
                    "flow_rules_installed": 20,
                    "meters_added": 0,
                    "apply_duration_ms": 4.2,
                },
            }
        },
        "previous_state": {
            "qos": {
                "sensor_to_edge_latency_ms": 1.0,
                "edge_to_cloud_latency_ms": 2.0,
                "throughput_bytes_per_second": 100.0,
                "loss_rate": 0.0,
            },
            "overhead": {
                "controller_active_actions": 0,
                "flow_rules_installed": 0,
                "meters_added": 0,
                "controller_apply_ms": 0.0,
            },
        },
        "next_state": {
            "scenario_id": "scenario1",
            "path_stage": 2,
            "path_stage_label": "worker",
            "attack_active": True,
            "attack_effect_success": True,
            "defense_success": False,
            "drop_rules_active": False,
            "counters_stopped": False,
            "raw_state_effects": {"drop_rules_active": False, "counters_stopped": False},
            "normalized_state_effects": {"drop_rules_active": True, "counters_stopped": True},
            "effect_resolution_policy": "semantic_confirmation_preferred_over_stale_raw_state",
            "qos": {
                "sensor_to_edge_latency_ms": 2.5,
                "edge_to_cloud_latency_ms": 3.5,
                "throughput_bytes_per_second": 80.0,
                "loss_rate": 0.1,
            },
            "overhead": {
                "controller_active_actions": 1,
                "flow_rules_installed": 20,
                "meters_added": 0,
                "controller_apply_ms": 4.2,
                "flow_delete_commands": 0,
                "total_cpu_seconds": 10.0,
                "total_memory_kb": 1024.0,
            },
        },
        "state_summary": {
            "scenario_id": "scenario1",
            "attack_active": True,
            "attack_effect_success": True,
            "attack_success": True,
            "defense_success": False,
            "gateway_seen": True,
            "worker_seen": True,
            "cloud_seen": False,
            "path_stage": 2,
            "path_stage_label": "worker",
            "controller_active_actions": 1,
            "flow_rules_installed": 20,
            "meters_added": 0,
            "drop_rules_active": True,
            "counters_stopped": True,
            "raw_drop_rules_active": False,
            "raw_counters_stopped": False,
            "normalized_drop_rules_active": True,
            "normalized_counters_stopped": True,
        },
        "stage_summary": {
            "scenario_id": "scenario1",
            "summary_text": "['Line one.', 'Line two.']",
            "qos_delta": {
                "sensor_to_edge_latency_ms": 1.5,
                "edge_to_cloud_latency_ms": 1.5,
                "throughput_bytes_per_second": -20.0,
                "loss_rate": 0.1,
            },
            "controller_delta": {
                "active_policy_actions_delta": 1.0,
                "flow_rules_installed_delta": 20.0,
                "meters_added_delta": 0.0,
                "controller_apply_ms_delta": 4.2,
            },
            "security_outcome": {"attack_effect_success": True, "defense_success": False},
            "llm": {
                "provider": "ollama",
                "model": "gemma4:e4b",
                "reasoning_summary": "Contain sen4.",
                "raw_llm_reasoning_summary": "Contain sen4.",
                "executed_decision_reasoning_summary": "Contain sen4.",
                "fallback_used": False,
                "raw_llm_selected_defender_strategy_id": "D1",
                "final_defender_strategy_id": "D1",
                "executed_via_fallback": False,
            },
            "execution": {
                "stage_valid": True,
                "stage_success": False,
                "defense_confirmed": True,
                "defender_status": "executed",
            },
            "stage_validation": {
                "comparable_stage": True,
                "llm_stage_valid": True,
                "stage_success": False,
                "defense_confirmed": True,
                "defense_applied": True,
                "llm_parse_success": True,
                "llm_recovery_used": False,
                "stage_kind": "experimental",
                "raw_state_effects": {"drop_rules_active": False, "counters_stopped": False},
                "normalized_state_effects": {"drop_rules_active": True, "counters_stopped": True},
                "effect_resolution_policy": "semantic_confirmation_preferred_over_stale_raw_state",
            },
        },
        "stage_validation": {
            "comparable_stage": True,
            "llm_stage_valid": True,
            "stage_success": False,
            "defense_confirmed": True,
            "defense_applied": True,
            "llm_parse_success": True,
            "llm_recovery_used": False,
            "stage_kind": "experimental",
            "raw_state_effects": {"drop_rules_active": False, "counters_stopped": False},
            "normalized_state_effects": {"drop_rules_active": True, "counters_stopped": True},
            "effect_resolution_policy": "semantic_confirmation_preferred_over_stale_raw_state",
        },
        "population_after": {"attacker": {"A1": 0.6}, "defender": {"D1": 0.7}},
        "llm": {
            "parse_success": True,
            "fallback_used": False,
            "recovery_used": False,
            "request_success": True,
            "reasoning_summary": "Contain sen4.",
            "raw_llm_reasoning_summary": "Contain sen4.",
            "executed_decision_reasoning_summary": "Contain sen4.",
            "raw_llm_selected_defender_strategy_id": "D1",
            "final_defender_strategy_id": "D1",
            "executed_via_fallback": False,
            "baseline_top_defender_strategy_id": "D0_observe",
        },
        "baseline_top_defender_strategy_id": "D0_observe",
    }


def _sample_decision_row() -> dict[str, object]:
    return {
        "stage_id": 1,
        "recorded_at": "2026-04-30T00:00:00Z",
        "scenario_id": "scenario1",
        "decision_source": "llm_defender",
        "selection": {
            "attacker": {"id": "A1"},
            "defender": {"id": "D1"},
        },
        "execution": {"attacker": {"status": "dispatched"}, "defender": {"status": "executed"}},
        "stage_summary": {"summary_text": "['Line one.', 'Line two.']"},
        "stage_validation": {"llm_parse_success": True},
        "state_summary": {
            "scenario_id": "scenario1",
            "attack_active": True,
            "attack_effect_success": True,
            "defense_success": False,
            "path_stage": 2,
        },
        "llm": {
            "provider": "ollama",
            "model": "gemma4:e4b",
            "reasoning_summary": "Contain sen4.",
            "raw_llm_reasoning_summary": "Contain sen4.",
            "executed_decision_reasoning_summary": "Contain sen4.",
            "raw_llm_selected_defender_strategy_id": "D1",
            "final_defender_strategy_id": "D1",
        },
    }


def _sample_summary_row() -> dict[str, object]:
    return {
        "stage_id": 1,
        "recorded_at": "2026-04-30T00:00:00Z",
        "scenario_id": "scenario1",
        "decision_source": "llm_defender",
        "attacker_strategy_id": "A1",
        "defender_strategy_id": "D1",
        "execution": {
            "defender_action": "quarantine_sensor",
            "defender_target": "sen4",
            "defender_status": "executed",
            "defense_confirmed": True,
            "defense_effects_confirmed": False,
            "stage_valid": True,
            "stage_success": False,
        },
        "stage_validation": {"defense_confirmed": True, "stage_success": False},
        "llm": {
            "provider": "ollama",
            "model": "gemma4:e4b",
            "reasoning_summary": "Contain sen4.",
            "raw_llm_reasoning_summary": "Contain sen4.",
            "executed_decision_reasoning_summary": "Contain sen4.",
            "fallback_used": False,
            "baseline_top_defender_strategy_id": "D1",
            "llm_selected_defender_strategy_id": "D1",
            "raw_llm_selected_defender_strategy_id": "D1",
            "final_defender_strategy_id": "D1",
            "executed_via_fallback": False,
            "llm_baseline_alignment": "followed",
            "llm_override_reason": "Matched the baseline top-utility defender.",
            "llm_ranked_candidates": [
                {
                    "strategy_id": "D1",
                    "baseline_utility_prior": 0.9,
                    "llm_rank": 1,
                    "expected_security_gain": 0.81,
                    "expected_qos_impact": 0.35,
                    "expected_controller_cost": 0.22,
                },
                {
                    "strategy_id": "D0",
                    "baseline_utility_prior": 0.1,
                    "llm_rank": 2,
                    "expected_security_gain": 0.12,
                    "expected_qos_impact": 0.01,
                    "expected_controller_cost": 0.0,
                },
            ],
        },
        "qos_delta": {
            "sensor_to_edge_latency_ms": 1.5,
            "edge_to_cloud_latency_ms": 1.5,
            "throughput_bytes_per_second": -20.0,
        },
        "controller_delta": {
            "flow_rules_installed_delta": 20.0,
            "meters_added_delta": 0.0,
            "controller_apply_ms_delta": 4.2,
        },
        "summary_text": "['Line one.', 'Line two.']",
    }
