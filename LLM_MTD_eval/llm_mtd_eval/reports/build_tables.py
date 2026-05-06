from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .load_results import normalize_summary_text
from .metric_mapping import formal_mapping_frame, pretty_method_label


TABLE_FILENAMES = {
    "environment_setup": "environment_setup.csv",
    "scenario_attack_setup": "scenario_attack_setup.csv",
    "formal_to_observable_mapping": "formal_to_observable_mapping.csv",
    "baseline_vs_llm_summary": "baseline_vs_llm_summary.csv",
    "stage_validity_summary": "stage_validity_summary.csv",
    "stage_case_study": "stage_case_study.csv",
    "llm_vs_baseline_decision_alignment": "llm_vs_baseline_decision_alignment.csv",
    "llm_candidate_ranking_case_study": "llm_candidate_ranking_case_study.csv",
}


def build_environment_setup_table(emo_root: Path) -> pd.DataFrame:
    network_model = _load_network_model(emo_root)
    sensor_nodes = sorted(getattr(network_model, "SENSOR_NODE_MAP", {}).keys())
    edge_nodes = sorted(getattr(network_model, "EDGE_NODE_MAP", {}).keys())
    cloud_nodes = sorted(getattr(network_model, "CLOUD_NODE_MAP", {}).keys())
    node_profiles = getattr(network_model, "NODE_RESOURCE_PROFILE", {})
    resource_profiles = getattr(network_model, "RESOURCE_PROFILES", {})
    gateway_nodes = [node for node in edge_nodes if node.endswith("_gw")]
    worker_nodes = [node for node in edge_nodes if node not in gateway_nodes]
    cloud_standard_nodes = [node for node in cloud_nodes if node_profiles.get(node) == "cloud_standard"]
    cloud_heavy_nodes = [node for node in cloud_nodes if node_profiles.get(node) == "cloud_heavy"]

    rows = [
        _environment_row(
            layer="sensor",
            node_group="sensor_nodes",
            nodes=sensor_nodes,
            role="Telemetry sources and candidate attacker footholds.",
            profile_name=node_profiles.get(sensor_nodes[0], ""),
            resource_profiles=resource_profiles,
            notes=f"{len(sensor_nodes)} constrained sensor containers distributed across edge subnets.",
        ),
        _environment_row(
            layer="edge",
            node_group="edge_gateways",
            nodes=gateway_nodes,
            role="Gateway forwarding, queueing, and first-hop defense enforcement.",
            profile_name=node_profiles.get(gateway_nodes[0], ""),
            resource_profiles=resource_profiles,
            notes=f"{len(gateway_nodes)} edge gateways bridge sensor traffic into worker paths.",
        ),
        _environment_row(
            layer="edge",
            node_group="edge_workers",
            nodes=worker_nodes,
            role="Edge compute workers servicing sensor-derived requests.",
            profile_name=node_profiles.get(worker_nodes[0], ""),
            resource_profiles=resource_profiles,
            notes=f"{len(worker_nodes)} workers represent service-side attack progression before cloud reachability.",
        ),
        _environment_row(
            layer="cloud",
            node_group="cloud_standard_services",
            nodes=cloud_standard_nodes,
            role="Shared cloud services for metrics, logging, and object workflows.",
            profile_name=node_profiles.get(cloud_standard_nodes[0], ""),
            resource_profiles=resource_profiles,
            notes=f"{len(cloud_standard_nodes)} standard cloud containers preserve moderate resource headroom.",
        ),
        _environment_row(
            layer="cloud",
            node_group="cloud_heavy_services",
            nodes=cloud_heavy_nodes,
            role="Heavier stateful and policy-intensive cloud services.",
            profile_name=node_profiles.get(cloud_heavy_nodes[0], ""),
            resource_profiles=resource_profiles,
            notes=f"{len(cloud_heavy_nodes)} higher-capacity services host the protected asset and policy engine.",
        ),
        {
            "layer": "control",
            "node_group": "ryu_controller",
            "node_examples": getattr(network_model, "CONTROLLER_NAME", "c0"),
            "role": "OpenFlow control plane and Ryu-based MTD action endpoint.",
            "cpu_profile": "host process",
            "memory_profile": "host process",
            "notes": (
                f"Listens on {getattr(network_model, 'CONTROLLER_IP', '127.0.0.1')}:"
                f"{getattr(network_model, 'CONTROLLER_PORT', 6653)} with REST actions exposed separately."
            ),
        },
    ]
    return pd.DataFrame(rows)


def build_scenario_attack_setup_table(
    emo_root: Path,
    *,
    scenario_filter: Iterable[str] | None = None,
) -> pd.DataFrame:
    scenario_ids = {item for item in (scenario_filter or []) if item}
    scenarios_path = emo_root / "integrations" / "attack_scenarios.json"
    strategy_space_path = emo_root / "integrations" / "strategy" / "strategy_space.json"
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8")) if scenarios_path.exists() else []
    strategy_space = json.loads(strategy_space_path.read_text(encoding="utf-8")) if strategy_space_path.exists() else {}
    defenders = list(strategy_space.get("defender_strategies", []))
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_id = scenario.get("scenario_id")
        if scenario_ids and scenario_id not in scenario_ids:
            continue
        candidate_defenders = [
            item for item in defenders if item.get("scenario_id") in {"*", scenario_id}
        ]
        rows.append(
            {
                "scenario_id": scenario_id,
                "entry_node": scenario.get("entry_node", ""),
                "mulval_path": " -> ".join(scenario.get("mulval_path", []) or []),
                "target_asset": scenario.get("target_asset", ""),
                "live_attack_type": scenario.get("live_attack_type", ""),
                "success_criteria": _format_success_criteria(scenario.get("success_criteria", {})),
                "candidate_defender_actions": _format_candidate_defender_actions(
                    scenario.get("candidate_defender_actions"),
                    candidate_defenders,
                ),
            }
        )
    return pd.DataFrame(rows)


def build_formal_to_observable_mapping_table() -> pd.DataFrame:
    return formal_mapping_frame()


def build_baseline_vs_llm_summary_table(stage_df: pd.DataFrame) -> pd.DataFrame:
    if stage_df.empty:
        return pd.DataFrame(
            columns=[
                "method",
                "scenario_id",
                "num_stages",
                "attack_success_rate",
                "defense_executed_rate",
                "defense_success_rate",
                "defense_confirmed_rate",
                "defense_effects_confirmed_rate",
                "mean_sensor_to_edge_latency_delta_ms",
                "mean_edge_to_cloud_latency_delta_ms",
                "mean_throughput_delta_bps",
                "mean_flow_rules_installed",
                "mean_meters_added",
                "mean_controller_apply_ms",
                "mean_llm_latency_ms",
                "llm_request_success_rate",
                "llm_parse_success_rate",
                "llm_fallback_rate",
                "paper_valid_stage_rate",
                "defense_applied_but_ineffective_rate",
            ]
        )
    if "comparable_stage" in stage_df.columns:
        comparable_mask = stage_df["comparable_stage"].fillna(False)
    else:
        comparable_mask = pd.Series([False] * len(stage_df), index=stage_df.index)
    comparable = stage_df.loc[comparable_mask].copy()
    if comparable.empty:
        comparable = stage_df.copy()
    grouped_rows: list[dict[str, Any]] = []
    for (method, scenario_id), group in comparable.groupby(["method", "scenario_id"], dropna=False):
        grouped_rows.append(
            {
                "method": pretty_method_label(str(method)),
                "scenario_id": scenario_id,
                "num_stages": int(len(group)),
                "attack_success_rate": _mean_bool(group, "attack_effect_success"),
                "defense_executed_rate": _mean_bool_nullable(group, "defense_executed"),
                "defense_success_rate": _mean_bool(group, "defense_success"),
                "defense_confirmed_rate": _mean_bool(group, "defense_confirmed"),
                "defense_effects_confirmed_rate": _mean_bool_nullable(group, "defense_effects_confirmed"),
                "mean_sensor_to_edge_latency_delta_ms": _mean_numeric(group, "sensor_to_edge_latency_delta_ms"),
                "mean_edge_to_cloud_latency_delta_ms": _mean_numeric(group, "edge_to_cloud_latency_delta_ms"),
                "mean_throughput_delta_bps": _mean_numeric(group, "throughput_delta_bps"),
                "mean_flow_rules_installed": _mean_numeric(group, "flow_rules_installed"),
                "mean_meters_added": _mean_numeric(group, "meters_added"),
                "mean_controller_apply_ms": _mean_numeric(group, "controller_apply_ms"),
                "mean_llm_latency_ms": _mean_numeric(group, "llm_latency_ms"),
                "llm_request_success_rate": _mean_bool_nullable(group, "llm_request_success"),
                "llm_parse_success_rate": _mean_bool_nullable(group, "llm_parse_success"),
                "llm_fallback_rate": _mean_bool_nullable(group, "llm_fallback_used"),
                "paper_valid_stage_rate": _mean_bool_nullable(group, "paper_valid_stage"),
                "defense_applied_but_ineffective_rate": _mean_bool_nullable(group, "defense_applied_but_not_effective"),
            }
        )
    return pd.DataFrame(grouped_rows).sort_values(["method", "scenario_id"]).reset_index(drop=True)


def build_stage_validity_summary_table(stage_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "method",
        "scenario_id",
        "total_stages",
        "paper_valid_stages",
        "fallback_only_stages",
        "timeout_failed_stages",
        "defense_applied_but_ineffective_stages",
        "defense_effect_mismatch_stages",
        "defense_executed_rate",
        "defense_confirmed_rate",
        "defense_effects_confirmed_rate",
        "defense_success_rate",
        "paper_valid_stage_rate",
    ]
    if stage_df.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (method, scenario_id), group in stage_df.groupby(["method", "scenario_id"], dropna=False):
        total = int(len(group))
        paper_valid = _count_bool(group, "paper_valid_stage")
        rows.append(
            {
                "method": pretty_method_label(str(method)),
                "scenario_id": scenario_id,
                "total_stages": total,
                "paper_valid_stages": paper_valid,
                "fallback_only_stages": _count_bool(group, "llm_fallback_used"),
                "timeout_failed_stages": _count_bool(group, "llm_timeout_failed"),
                "defense_applied_but_ineffective_stages": _count_bool(group, "defense_applied_but_not_effective"),
                "defense_effect_mismatch_stages": _count_bool(group, "defense_effect_mismatch"),
                "defense_executed_rate": _mean_bool_nullable(group, "defense_executed"),
                "defense_confirmed_rate": _mean_bool_nullable(group, "defense_confirmed"),
                "defense_effects_confirmed_rate": _mean_bool_nullable(group, "defense_effects_confirmed"),
                "defense_success_rate": _mean_bool_nullable(group, "defense_success"),
                "paper_valid_stage_rate": paper_valid / total if total else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(["method", "scenario_id"]).reset_index(drop=True)


def build_stage_case_study_table(
    eval_stage_df: pd.DataFrame,
    combined_stage_df: pd.DataFrame,
    decision_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    source_df = eval_stage_df if not eval_stage_df.empty else combined_stage_df
    if source_df.empty:
        return pd.DataFrame(
            columns=[
                "stage_id",
                "scenario_id",
                "decision_source",
                "attacker_strategy_id",
                "defender_selected",
                "raw_llm_selected_defender_strategy_id",
                "final_defender_strategy_id",
                "defender_executed",
                "defense_executed",
                "defender_action",
                "defender_target",
                "llm_reasoning_summary",
                "raw_llm_reasoning_summary",
                "executed_decision_reasoning_summary",
                "llm_decision_mode",
                "llm_telemetry_confidence",
                "llm_repeat_previous_action",
                "executed_via_fallback",
                "fallback_reason",
                "execution_mode",
                "stage_valid",
                "comparable_stage",
                "attack_active",
                "path_stage",
                "attack_effect_success",
                "defense_success",
                "defense_confirmed",
                "defense_effects_confirmed",
                "defense_applied_but_not_effective",
                "semantic_observed_defense_effects",
                "semantic_missing_defense_effects",
                "baseline_top_defender_strategy_id",
                "baseline_top_defender_population_share",
                "baseline_top_defender_tiebreak_reason",
                "flow_rules_installed",
                "meters_added",
                "sensor_to_edge_latency_ms",
                "edge_to_cloud_latency_ms",
                "throughput_bps",
                "summary_text",
            ]
        )
    table = source_df.copy()
    if decision_df is not None and not decision_df.empty:
        attacker_lookup = (
            decision_df[["scenario_id", "stage_id", "attacker_strategy_id"]]
            .dropna(subset=["scenario_id", "stage_id"])
            .drop_duplicates(subset=["scenario_id", "stage_id"], keep="last")
            .rename(columns={"attacker_strategy_id": "attacker_strategy_id_from_trace"})
        )
        table = table.merge(attacker_lookup, on=["scenario_id", "stage_id"], how="left")
        table["attacker_strategy_id"] = table["attacker_strategy_id"].fillna(table["attacker_strategy_id_from_trace"])
        table["attacker_strategy_fallback_source"] = table["attacker_strategy_fallback_source"].where(
            table["attacker_strategy_id"].notna(), ""
        )
        missing_mask = table["attacker_strategy_fallback_source"].eq("") & table["attacker_strategy_id_from_trace"].notna()
        table.loc[missing_mask, "attacker_strategy_fallback_source"] = "decision_trace"
        table = table.drop(columns=["attacker_strategy_id_from_trace"])

    effective_defense_success = table["defense_success"].fillna(False).astype(bool)
    observe_mask = table["defender_action"].fillna("observe").eq("observe")
    if "non_intervention_success" in table.columns:
        effective_defense_success = effective_defense_success & (~observe_mask | table["non_intervention_success"].fillna(False).astype(bool))
    else:
        effective_defense_success = effective_defense_success & (~observe_mask)
    table["defense_success"] = effective_defense_success

    case_columns = [
            "stage_id",
            "scenario_id",
            "decision_source",
            "attacker_strategy_id",
            "defender_selected",
            "raw_llm_selected_defender_strategy_id",
            "final_defender_strategy_id",
            "defender_executed",
            "defense_executed",
            "defender_action",
            "defender_target",
            "llm_reasoning_summary",
            "raw_llm_reasoning_summary",
            "executed_decision_reasoning_summary",
            "llm_decision_mode",
            "llm_telemetry_confidence",
            "llm_repeat_previous_action",
            "executed_via_fallback",
            "fallback_reason",
            "execution_mode",
            "stage_valid",
            "comparable_stage",
            "attack_active",
            "path_stage",
            "attack_effect_success",
            "defense_success",
            "defense_confirmed",
            "defense_effects_confirmed",
            "defense_applied_but_not_effective",
            "semantic_observed_defense_effects",
            "semantic_missing_defense_effects",
            "baseline_top_defender_strategy_id",
            "baseline_top_defender_population_share",
            "baseline_top_defender_tiebreak_reason",
            "flow_rules_installed",
            "meters_added",
            "sensor_to_edge_latency_ms",
            "edge_to_cloud_latency_ms",
            "throughput_bps",
            "summary_text",
    ]
    for column in case_columns:
        if column not in table.columns:
            table[column] = ""
    table = table[case_columns].copy()
    table["summary_text"] = table.apply(_canonical_case_study_summary, axis=1)
    return table.sort_values(["scenario_id", "stage_id"]).reset_index(drop=True)


def build_llm_vs_baseline_decision_alignment_table(eval_stage_df: pd.DataFrame) -> pd.DataFrame:
    if eval_stage_df.empty:
        return pd.DataFrame(
            columns=[
                "scenario_id",
                "stage_id",
                "baseline_top_defender",
                "baseline_top_defender_population_share",
                "baseline_top_defender_tiebreak_reason",
                "llm_selected_defender",
                "raw_llm_alignment",
                "final_executed_alignment",
                "final_defender_strategy_id",
                "executed_via_fallback",
                "fallback_reason",
                "raw_llm_reasoning_summary",
                "executed_decision_reasoning_summary",
                "llm_decision_mode",
                "llm_telemetry_confidence",
                "llm_repeat_previous_action",
                "aligned",
                "override_reason",
                "defense_executed",
                "defense_confirmed",
                "defense_effects_confirmed",
                "defense_success",
                "defense_applied_but_not_effective",
                "attack_effect_success",
            ]
        )
    columns = [
        "scenario_id",
        "stage_id",
        "baseline_top_defender_strategy_id",
        "baseline_top_defender_population_share",
        "baseline_top_defender_tiebreak_reason",
        "llm_selected_defender_strategy_id",
        "raw_llm_alignment",
        "final_executed_alignment",
        "final_defender_strategy_id",
        "executed_via_fallback",
        "fallback_reason",
        "raw_llm_reasoning_summary",
        "executed_decision_reasoning_summary",
        "llm_decision_mode",
        "llm_telemetry_confidence",
        "llm_repeat_previous_action",
        "llm_baseline_alignment",
        "llm_override_reason",
        "defense_executed",
        "defense_confirmed",
        "defense_effects_confirmed",
        "defense_success",
        "defense_applied_but_not_effective",
        "attack_effect_success",
    ]
    table = eval_stage_df.copy()
    for column in columns:
        if column not in table.columns:
            table[column] = ""
    table = table[columns].copy()
    table = table.rename(
        columns={
            "baseline_top_defender_strategy_id": "baseline_top_defender",
            "llm_selected_defender_strategy_id": "llm_selected_defender",
            "llm_override_reason": "override_reason",
        }
    )
    table["aligned"] = table["llm_selected_defender"].fillna("").eq(table["baseline_top_defender"].fillna(""))
    if "raw_llm_alignment" not in table.columns:
        table["raw_llm_alignment"] = table["aligned"].map({True: "followed", False: "overrode"})
    if "final_executed_alignment" not in table.columns:
        table["final_executed_alignment"] = table["final_defender_strategy_id"].fillna("").eq(
            table["baseline_top_defender"].fillna("")
        ).map({True: "followed", False: "overrode"})
    return table.sort_values(["scenario_id", "stage_id"]).reset_index(drop=True)


def build_llm_candidate_ranking_case_study_table(eval_stage_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "scenario_id",
        "stage_id",
        "candidate_strategy_id",
        "baseline_utility_prior",
        "llm_rank_rank",
        "llm_selected",
        "expected_security_gain",
        "expected_qos_impact",
        "expected_controller_cost",
    ]
    if eval_stage_df.empty or "llm_ranked_candidates" not in eval_stage_df.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for row in eval_stage_df.to_dict(orient="records"):
        ranked = row.get("llm_ranked_candidates") or []
        if not isinstance(ranked, list):
            continue
        selected_id = str(row.get("llm_selected_defender_strategy_id") or row.get("defender_selected") or "")
        for index, candidate in enumerate(ranked, start=1):
            if not isinstance(candidate, dict):
                continue
            strategy_id = str(candidate.get("strategy_id") or candidate.get("id") or "").strip()
            if not strategy_id:
                continue
            rows.append(
                {
                    "scenario_id": row.get("scenario_id"),
                    "stage_id": row.get("stage_id"),
                    "candidate_strategy_id": strategy_id,
                    "baseline_utility_prior": _coerce_csv_value(candidate.get("baseline_utility_prior")),
                    "llm_rank_rank": _coerce_csv_value(candidate.get("llm_rank", index)),
                    "llm_selected": strategy_id == selected_id,
                    "expected_security_gain": _coerce_csv_value(
                        candidate.get("expected_security_gain", candidate.get("estimated_security_gain_proxy"))
                    ),
                    "expected_qos_impact": _coerce_csv_value(
                        candidate.get("expected_qos_impact", candidate.get("estimated_qos_cost_proxy"))
                    ),
                    "expected_controller_cost": _coerce_csv_value(
                        candidate.get("expected_controller_cost", candidate.get("estimated_controller_cost_proxy"))
                    ),
                }
            )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["scenario_id", "stage_id", "llm_rank_rank"]).reset_index(drop=True)


def _canonical_case_study_summary(row: pd.Series) -> str:
    existing = normalize_summary_text(row.get("summary_text"))
    attacker = str(row.get("attacker_strategy_id") or "unknown_attacker")
    defender = str(
        row.get("final_defender_strategy_id")
        or row.get("defender_selected")
        or row.get("defender_strategy_id")
        or "unknown_defender"
    )
    action = str(row.get("defender_action") or "observe")
    executed = str(row.get("defender_executed") or row.get("execution_mode") or "unknown")
    confirmed = bool(row.get("defense_confirmed"))
    effects_confirmed = bool(row.get("defense_effects_confirmed"))
    ineffective = bool(row.get("defense_applied_but_not_effective"))
    defense_success = bool(row.get("defense_success"))
    attack_effect_success = bool(row.get("attack_effect_success"))
    rationale = str(
        row.get("executed_decision_reasoning_summary")
        or row.get("llm_reasoning_summary")
        or ""
    ).strip()
    stage_id = row.get("stage_id")
    scenario_id = str(row.get("scenario_id") or "unknown_scenario")
    path_stage = row.get("path_stage")
    if defense_success:
        outcome = "security progression was reduced"
    elif ineffective:
        outcome = "defense was confirmed but did not stop the attack effect"
    elif attack_effect_success:
        outcome = "attack remained effective"
    else:
        outcome = "attack pressure reduced"
    canonical = (
        f"Stage {stage_id} for {scenario_id}: attacker {attacker} met defender {defender}. "
        f"Action {action} was {executed}; defense_confirmed={confirmed}; "
        f"defense_effects_confirmed={effects_confirmed}; defense_success={defense_success}. "
        f"Path stage={path_stage}. Outcome: {outcome}."
    )
    if rationale:
        canonical += f" Rationale: {rationale}"
    if not existing:
        return canonical
    lowered = existing.lower()
    required_tokens = ("defense_confirmed", "defense_effects_confirmed", "defense_success", "attacker", "defender")
    if any(token not in lowered for token in required_tokens):
        return canonical
    return existing


def write_tables(
    *,
    output_dir: Path,
    emo_root: Path,
    eval_stage_df: pd.DataFrame,
    combined_stage_df: pd.DataFrame,
    eval_decision_df: pd.DataFrame | None = None,
    raw_stage_df: pd.DataFrame | None = None,
    scenario_filter: Iterable[str] | None = None,
) -> dict[str, Path]:
    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "environment_setup": build_environment_setup_table(emo_root),
        "scenario_attack_setup": build_scenario_attack_setup_table(emo_root, scenario_filter=scenario_filter),
        "formal_to_observable_mapping": build_formal_to_observable_mapping_table(),
        "baseline_vs_llm_summary": build_baseline_vs_llm_summary_table(combined_stage_df),
        "stage_validity_summary": build_stage_validity_summary_table(raw_stage_df if raw_stage_df is not None else combined_stage_df),
        "stage_case_study": build_stage_case_study_table(eval_stage_df, combined_stage_df, eval_decision_df),
        "llm_vs_baseline_decision_alignment": build_llm_vs_baseline_decision_alignment_table(eval_stage_df),
        "llm_candidate_ranking_case_study": build_llm_candidate_ranking_case_study_table(eval_stage_df),
    }
    written: dict[str, Path] = {}
    for key, frame in tables.items():
        path = tables_dir / TABLE_FILENAMES[key]
        frame.to_csv(path, index=False)
        written[key] = path
    return written


def _load_network_model(emo_root: Path) -> Any:
    module_path = emo_root / "network_model.py"
    spec = importlib.util.spec_from_file_location("llm_mtd_emo_network_model", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load network model from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _environment_row(
    *,
    layer: str,
    node_group: str,
    nodes: list[str],
    role: str,
    profile_name: str,
    resource_profiles: dict[str, Any],
    notes: str,
) -> dict[str, Any]:
    profile = resource_profiles.get(profile_name, {})
    return {
        "layer": layer,
        "node_group": node_group,
        "node_examples": ", ".join(nodes[:3]),
        "role": role,
        "cpu_profile": profile.get("cpu", ""),
        "memory_profile": profile.get("memory", ""),
        "notes": notes,
    }


def _format_success_criteria(criteria: dict[str, Any]) -> str:
    if not isinstance(criteria, dict):
        return ""
    parts: list[str] = []
    for key in ("gateway_seen", "worker_requests_increase", "cloud_summary_rate_changes"):
        if criteria.get(key) is True:
            parts.append(key)
    thresholds = criteria.get("attack_effect_thresholds", {})
    if isinstance(thresholds, dict) and thresholds:
        threshold_bits = [f"{key}={value}" for key, value in thresholds.items()]
        parts.append("thresholds(" + ", ".join(threshold_bits) + ")")
    checklist = criteria.get("checklist", [])
    if isinstance(checklist, list) and checklist:
        checklist_ids = [str(item.get("id", "")).strip() for item in checklist if isinstance(item, dict)]
        if checklist_ids:
            parts.append("checklist(" + ", ".join(checklist_ids) + ")")
    return "; ".join(parts)


def _format_candidate_defender_actions(
    scenario_actions: Any,
    strategy_space_actions: list[dict[str, Any]],
) -> str:
    if isinstance(scenario_actions, list) and scenario_actions:
        readable: list[str] = []
        for item in scenario_actions:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "")).strip()
            if not action:
                continue
            parts = [action]
            target = str(item.get("target", "")).strip()
            if target:
                parts.append(f"target={target}")
            extras = [
                f"{key}={value}"
                for key, value in item.items()
                if key not in {"action", "target"} and value not in ("", None, [])
            ]
            if extras:
                parts.extend(extras)
            readable.append("(".join([parts[0], ", ".join(parts[1:]) + ")"]) if len(parts) > 1 else parts[0])
        if readable:
            return "; ".join(readable)
    return "; ".join(f"{item.get('id')}:{item.get('action')}" for item in strategy_space_actions if item.get("action"))


def _coerce_csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _mean_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.mean())


def _mean_bool(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    series = frame[column].dropna().astype(float)
    if series.empty:
        return None
    return float(series.mean())


def _count_bool(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    series = frame[column].dropna()
    if series.empty:
        return 0
    return int(series.astype(bool).sum())


def _mean_bool_nullable(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    series = frame[column].dropna()
    if series.empty:
        return None
    return float(series.astype(float).mean())
