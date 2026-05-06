from __future__ import annotations

import ast
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


@dataclass(slots=True)
class ResultFrames:
    stage_df: pd.DataFrame
    decision_df: pd.DataFrame
    summary_df: pd.DataFrame
    population_df: pd.DataFrame
    population_evolution_df: pd.DataFrame


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_summary_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip()).strip()
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    if not isinstance(value, str):
        return str(value)
    text = value.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except Exception:
                continue
            return normalize_summary_text(parsed)
    return text


def _canonical_alignment_fields(
    *,
    baseline_top_defender_strategy_id: Any,
    raw_llm_selected_defender_strategy_id: Any,
    final_defender_strategy_id: Any,
) -> dict[str, str]:
    baseline_id = str(baseline_top_defender_strategy_id or "").strip()
    raw_id = str(raw_llm_selected_defender_strategy_id or "").strip()
    final_id = str(final_defender_strategy_id or "").strip()

    def classify(selected_id: str) -> str:
        if not baseline_id or not selected_id:
            return "unknown"
        return "followed" if selected_id == baseline_id else "overrode"

    raw_alignment = classify(raw_id)
    final_alignment = classify(final_id)
    return {
        "raw_llm_alignment": raw_alignment,
        "final_executed_alignment": final_alignment,
        "llm_baseline_alignment": raw_alignment,
    }


def _effective_state_flag(state_summary: dict[str, Any], next_state: dict[str, Any], flag_name: str) -> bool:
    return _coerce_bool(
        state_summary.get(f"normalized_{flag_name}"),
        state_summary.get(flag_name),
        _nested_get(next_state, "normalized_state_effects", flag_name),
        next_state.get(f"normalized_{flag_name}"),
        next_state.get(flag_name),
        False,
    )


def load_result_frames(
    *,
    method: str,
    stage_history_path: Path | None = None,
    decision_trace_path: Path | None = None,
    stage_summaries_path: Path | None = None,
    population_path: Path | None = None,
    scenario_filter: Iterable[str] | None = None,
) -> ResultFrames:
    scenario_ids = {item for item in (scenario_filter or []) if item}
    trace_index = discover_stage_trace_metrics(stage_history_path)
    stage_rows = [
        _flatten_stage_row(row, method=method, trace_index=trace_index)
        for row in read_jsonl(stage_history_path)
    ]
    decision_rows = [
        _flatten_decision_row(row, method=method, trace_index=trace_index)
        for row in read_jsonl(decision_trace_path)
    ]
    summary_rows = [
        _flatten_summary_row(row, method=method)
        for row in read_jsonl(stage_summaries_path)
    ]
    population_rows = _flatten_population_state(read_json(population_path), method=method)

    stage_df = pd.DataFrame(stage_rows)
    decision_df = pd.DataFrame(decision_rows)
    summary_df = pd.DataFrame(summary_rows)
    population_df = pd.DataFrame(population_rows)

    if scenario_ids:
        stage_df = _filter_by_scenario(stage_df, scenario_ids)
        decision_df = _filter_by_scenario(decision_df, scenario_ids)
        summary_df = _filter_by_scenario(summary_df, scenario_ids)
        population_df = _filter_by_scenario(population_df, scenario_ids)

    population_evolution_df = expand_population_history(stage_df)
    return ResultFrames(
        stage_df=stage_df.reset_index(drop=True),
        decision_df=decision_df.reset_index(drop=True),
        summary_df=summary_df.reset_index(drop=True),
        population_df=population_df.reset_index(drop=True),
        population_evolution_df=population_evolution_df.reset_index(drop=True),
    )


def concat_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    materialized = [frame for frame in frames if frame is not None and not frame.empty]
    if not materialized:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return pd.concat(materialized, ignore_index=True, sort=False)


def expand_population_history(stage_df: pd.DataFrame) -> pd.DataFrame:
    if stage_df.empty:
        return pd.DataFrame(
            columns=[
                "method",
                "scenario_id",
                "stage_id",
                "recorded_at",
                "role",
                "strategy_id",
                "population_share",
            ]
        )
    rows: list[dict[str, Any]] = []
    for row in stage_df.to_dict(orient="records"):
        for role, field_name in (
            ("attacker", "attacker_population_after"),
            ("defender", "defender_population_after"),
        ):
            population = row.get(field_name) or {}
            if not isinstance(population, dict):
                continue
            for strategy_id, share in population.items():
                rows.append(
                    {
                        "method": row.get("method"),
                        "scenario_id": row.get("scenario_id"),
                        "stage_id": row.get("stage_id"),
                        "recorded_at": row.get("recorded_at"),
                        "role": role,
                        "strategy_id": strategy_id,
                        "population_share": _as_float(share),
                    }
                )
    return pd.DataFrame(rows)


def discover_stage_trace_metrics(stage_history_path: Path | None) -> dict[tuple[str, int], dict[str, Any]]:
    if stage_history_path is None or not stage_history_path.exists():
        return {}
    raw_dir = stage_history_path.parent
    trace_dir = raw_dir.parent / "traces"
    if not raw_dir.exists() or not trace_dir.exists():
        return {}
    index: dict[tuple[str, int], dict[str, Any]] = {}
    for raw_path in sorted(raw_dir.glob("stage_*.json")):
        payload = read_json(raw_path)
        transition = payload.get("transition", {})
        stage_id = _as_int(
            transition.get("stage_id"),
            _nested_get(transition, "stage_summary", "stage_id"),
        )
        scenario_id = (
            _nested_get(transition, "stage_summary", "scenario_id")
            or _nested_get(transition, "state_summary", "scenario_id")
            or _nested_get(transition, "selection", "attacker", "scenario_id")
        )
        run_id = str(payload.get("run_id") or raw_path.stem)
        if stage_id is None or not scenario_id:
            continue
        defender_trace = read_json(trace_dir / f"{run_id}_defender_trace.json")
        summary_trace = read_json(trace_dir / f"{run_id}_summary_trace.json")
        index[(str(scenario_id), stage_id)] = {
            "llm_latency_ms": _as_float(defender_trace.get("latency_ms")),
            "llm_retries_used": _as_float(defender_trace.get("retries_used")),
            "llm_trace_provider": defender_trace.get("provider"),
            "llm_trace_model": defender_trace.get("model_name"),
            "summary_latency_ms": _as_float(summary_trace.get("latency_ms")),
        }
    return index


def _flatten_stage_row(
    row: dict[str, Any],
    *,
    method: str,
    trace_index: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    scenario_id = _scenario_id_from_row(row)
    stage_id = _as_int(row.get("stage_id"))
    selection_attacker = _nested_get(row, "selection", "attacker", default={}) or {}
    selection_defender = _nested_get(row, "selection", "defender", default={}) or {}
    execution_attacker = _nested_get(row, "execution", "attacker", default={}) or {}
    execution_defender = _nested_get(row, "execution", "defender", default={}) or {}
    execution_summary_defender = _nested_get(row, "execution_summary", "defender", default={}) or {}
    previous_state = row.get("previous_state", {}) or {}
    next_state = row.get("next_state", {}) or {}
    stage_summary = row.get("stage_summary", {}) or {}
    stage_validation = _merge_dicts(stage_summary.get("stage_validation", {}), row.get("stage_validation", {}))
    state_summary = row.get("state_summary", {}) or {}
    llm = _merge_dicts(stage_summary.get("llm", {}), row.get("llm", {}))
    trace_metrics = trace_index.get((scenario_id, stage_id or -1), {})
    raw_llm_selected_strategy_id = str(
        row.get("raw_llm_selected_defender_strategy_id")
        or llm.get("raw_llm_selected_defender_strategy_id")
        or row.get("llm_selected_defender_strategy_id")
        or llm.get("llm_selected_defender_strategy_id")
        or selection_defender.get("id")
        or ""
    )
    final_defender_strategy_id = str(
        row.get("final_defender_strategy_id")
        or llm.get("final_defender_strategy_id")
        or row.get("executed_defender_strategy_id")
        or llm.get("executed_defender_strategy_id")
        or selection_defender.get("id")
        or ""
    )
    baseline_top_strategy_id = str(
        row.get("baseline_top_defender_strategy_id")
        or llm.get("baseline_top_defender_strategy_id")
        or _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "strategy_id")
        or _nested_get(row, "baseline_game_prior", "defender", "id")
        or ""
    )
    llm_selected_strategy_id = raw_llm_selected_strategy_id
    llm_ranked_candidates = (
        row.get("llm_ranked_candidates")
        or llm.get("llm_ranked_candidates")
        or selection_defender.get("ranked_candidates")
        or []
    )
    canonical_baseline = _canonical_baseline_from_ranked_candidates(llm_ranked_candidates)
    if canonical_baseline:
        baseline_top_strategy_id = str(canonical_baseline.get("strategy_id") or baseline_top_strategy_id)
    alignment_fields = _canonical_alignment_fields(
        baseline_top_defender_strategy_id=baseline_top_strategy_id,
        raw_llm_selected_defender_strategy_id=llm_selected_strategy_id,
        final_defender_strategy_id=final_defender_strategy_id,
    )
    baseline_alignment = alignment_fields["llm_baseline_alignment"]
    override_reason = str(
        row.get("llm_override_reason")
        or llm.get("llm_override_reason")
        or selection_defender.get("override_reason")
        or ""
    )
    defense_status = (
        stage_validation.get("defender_execution_status")
        or execution_summary_defender.get("status")
        or execution_defender.get("status")
        or _nested_get(stage_summary, "execution", "defender_status")
        or ""
    )
    execution_mode = (
        "executed"
        if defense_status == "executed"
        else ("dry_run" if defense_status == "dry_run" else ("observe_only" if defense_status == "observe_only" else defense_status))
    )
    ryu_response = execution_summary_defender.get("ryu_response", {}) or {}
    ryu_body = _parse_json_body(_nested_get(execution_defender, "post_result", "body"))
    qos_delta = _normalize_qos_delta(stage_summary.get("qos_delta", {}), previous_state, next_state)
    controller_delta = _normalize_controller_delta(
        stage_summary.get("controller_delta", {}),
        previous_state,
        next_state,
    )

    attacker_status = (
        execution_attacker.get("status")
        or _nested_get(row, "execution_summary", "attacker", "status")
        or ""
    )
    stage_kind = (
        stage_validation.get("stage_kind")
        or _nested_get(stage_summary, "execution", "stage_kind")
        or _infer_stage_kind(attacker_status, defense_status)
    )
    comparable_stage = _coerce_bool(
        stage_validation.get("comparable_stage"),
        stage_kind != "warmup" and attacker_status != "dry_run" and defense_status != "dry_run",
    )
    stage_valid = _coerce_bool(
        _nested_get(stage_summary, "execution", "stage_valid"),
        stage_validation.get("llm_stage_valid"),
        comparable_stage,
    )
    expected_effects = list(stage_validation.get("expected_defense_effects", []) or [])
    semantic_effects = _normalize_semantic_effects_for_report(
        expected_effects=expected_effects,
        stage_validation=stage_validation,
        defender_action=(
            selection_defender.get("action")
            or execution_summary_defender.get("action")
            or _nested_get(stage_summary, "execution", "defender_action")
            or ""
        ),
        defender_target=(
            selection_defender.get("target")
            or execution_summary_defender.get("target")
            or _nested_get(stage_summary, "execution", "defender_target")
            or ""
        ),
        previous_state=previous_state,
        next_state=next_state,
    )
    attack_effect_success_value = _coerce_bool(
        state_summary.get("attack_effect_success"),
        _nested_get(stage_summary, "security_outcome", "attack_effect_success"),
        False,
    )
    defense_confirmed_value = _coerce_bool(
        stage_validation.get("defense_confirmed"),
        _nested_get(stage_summary, "execution", "defense_confirmed"),
        False,
    )
    defense_executed_value = _coerce_bool(stage_validation.get("defense_executed"), defense_status == "executed")
    defense_effects_confirmed_value = (
        len(semantic_effects["missing"]) == 0
        if expected_effects
        else _coerce_bool(
            stage_validation.get("defense_effects_confirmed"),
            _nested_get(stage_summary, "execution", "defense_effects_confirmed"),
            False,
        )
    )
    defense_success_value = False if attack_effect_success_value else _coerce_bool(
        state_summary.get("defense_success"),
        _nested_get(stage_summary, "security_outcome", "defense_success"),
        False,
    )
    defense_applied_but_not_effective_value = bool(
        _coerce_bool(stage_validation.get("defense_applied_but_not_effective"), False)
        or (defense_executed_value and defense_confirmed_value and not defense_success_value)
    )

    return {
        "method": method,
        "scenario_id": scenario_id,
        "stage_id": stage_id,
        "recorded_at": row.get("recorded_at"),
        "transition_id": row.get("transition_id"),
        "decision_source": row.get("decision_source", "game_baseline"),
        "attacker_strategy_id": selection_attacker.get("id") or _nested_get(row, "inference", "attacker_strategy_id"),
        "attacker_strategy_name": selection_attacker.get("name"),
        "attacker_execution_status": attacker_status,
        "attacker_population_after": row.get("population_after", {}).get("attacker", {}),
        "attacker_strategy_fallback_source": "stage_history_selection" if selection_attacker.get("id") else "",
        "defender_strategy_id": selection_defender.get("id") or _nested_get(row, "inference", "defender_strategy_id"),
        "defender_strategy_name": selection_defender.get("name"),
        "defender_action": (
            selection_defender.get("action")
            or execution_summary_defender.get("action")
            or _nested_get(stage_summary, "execution", "defender_action")
        ),
        "defender_target": (
            selection_defender.get("target")
            or execution_summary_defender.get("target")
            or _nested_get(stage_summary, "execution", "defender_target")
        ),
        "defender_execution_status": defense_status,
        "defender_population_after": row.get("population_after", {}).get("defender", {}),
        "stage_kind": stage_kind,
        "comparable_stage": comparable_stage,
        "stage_valid": stage_valid,
        "execution_mode": execution_mode,
        "defender_selected": llm_selected_strategy_id or selection_defender.get("id") or "",
        "raw_llm_selected_defender_strategy_id": raw_llm_selected_strategy_id,
        "final_defender_strategy_id": final_defender_strategy_id,
        "defender_executed": defense_status,
        "stage_success": _coerce_bool(
            stage_validation.get("stage_success"),
            _nested_get(stage_summary, "execution", "stage_success"),
            False,
        ),
        "llm_stage_valid": _coerce_bool(stage_validation.get("llm_stage_valid"), stage_valid),
        "paper_valid_stage": _coerce_bool(stage_validation.get("paper_valid_stage"), stage_validation.get("llm_stage_valid"), stage_valid),
        "learning_valid_stage": _coerce_bool(stage_validation.get("learning_valid_stage"), False),
        "operationally_executed_debug_stage": _coerce_bool(
            stage_validation.get("operationally_executed_debug_stage"),
            defense_status == "executed" and not _coerce_bool(stage_validation.get("paper_valid_stage"), False),
            False,
        ),
        "invalidity_reasons": list(stage_validation.get("invalidity_reasons", []) or []),
        "llm_stage_success": _coerce_bool(stage_validation.get("llm_stage_success"), False),
        "execution_stage_success": _coerce_bool(stage_validation.get("execution_stage_success"), False),
        "security_stage_success": _coerce_bool(stage_validation.get("security_stage_success"), False),
        "defense_applied": _coerce_bool(stage_validation.get("defense_applied"), False),
        "defense_executed": defense_executed_value,
        "defense_applied_but_not_effective": defense_applied_but_not_effective_value,
        "defense_confirmed": defense_confirmed_value,
        "defense_effects_confirmed": defense_effects_confirmed_value,
        "defense_effect_mismatch": bool(defense_confirmed_value and expected_effects and not defense_effects_confirmed_value),
        "effect_source_conflict": _coerce_bool(stage_validation.get("effect_source_conflict"), False),
        "effect_source_details": stage_validation.get("effect_source_details", {}) or {},
        "expected_defense_effects": expected_effects,
        "observed_defense_effects": semantic_effects["observed"],
        "missing_defense_effects": semantic_effects["missing"],
        "semantic_observed_defense_effects": semantic_effects["observed"],
        "semantic_missing_defense_effects": semantic_effects["missing"],
        "semantic_effect_mapping_details": semantic_effects["details"],
        "attack_active": _coerce_bool(state_summary.get("attack_active"), _nested_get(next_state, "attack_active"), False),
        "attack_success": _coerce_bool(state_summary.get("attack_success"), False),
        "attack_effect_success": attack_effect_success_value,
        "gateway_seen": _coerce_bool(state_summary.get("gateway_seen"), False),
        "worker_seen": _coerce_bool(state_summary.get("worker_seen"), False),
        "cloud_seen": _coerce_bool(state_summary.get("cloud_seen"), False),
        "path_stage": _as_float(state_summary.get("path_stage"), _nested_get(next_state, "path_stage")),
        "path_stage_label": state_summary.get("path_stage_label") or _nested_get(next_state, "path_stage_label"),
        "defense_success": defense_success_value,
        "non_intervention_success": _coerce_bool(stage_validation.get("non_intervention_success"), False),
        "drop_rules_active": _effective_state_flag(state_summary, next_state, "drop_rules_active"),
        "counters_stopped": _effective_state_flag(state_summary, next_state, "counters_stopped"),
        "raw_drop_rules_active": _coerce_bool(
            state_summary.get("raw_drop_rules_active"),
            _nested_get(next_state, "raw_state_effects", "drop_rules_active"),
            False,
        ),
        "raw_counters_stopped": _coerce_bool(
            state_summary.get("raw_counters_stopped"),
            _nested_get(next_state, "raw_state_effects", "counters_stopped"),
            False,
        ),
        "normalized_drop_rules_active": _coerce_bool(
            state_summary.get("normalized_drop_rules_active"),
            _nested_get(next_state, "normalized_state_effects", "drop_rules_active"),
            False,
        ),
        "normalized_counters_stopped": _coerce_bool(
            state_summary.get("normalized_counters_stopped"),
            _nested_get(next_state, "normalized_state_effects", "counters_stopped"),
            False,
        ),
        "sensor_to_edge_latency_ms": _as_float(_nested_get(next_state, "qos", "sensor_to_edge_latency_ms")),
        "edge_to_cloud_latency_ms": _as_float(_nested_get(next_state, "qos", "edge_to_cloud_latency_ms")),
        "throughput_bps": _as_float(_nested_get(next_state, "qos", "throughput_bytes_per_second")),
        "loss_rate": _as_float(_nested_get(next_state, "qos", "loss_rate")),
        "sensor_to_edge_latency_delta_ms": qos_delta["sensor_to_edge_latency_delta_ms"],
        "edge_to_cloud_latency_delta_ms": qos_delta["edge_to_cloud_latency_delta_ms"],
        "throughput_delta_bps": qos_delta["throughput_delta_bps"],
        "loss_rate_delta": qos_delta["loss_rate_delta"],
        "controller_active_actions": _as_float(
            ryu_response.get("active_policy_actions"),
            ryu_body.get("active_policy_actions"),
            state_summary.get("controller_active_actions"),
            _nested_get(next_state, "overhead", "controller_active_actions"),
        ),
        "flow_rules_installed": _as_float(
            ryu_response.get("flow_rules_installed"),
            ryu_body.get("flow_rules_installed"),
            state_summary.get("flow_rules_installed"),
            _nested_get(next_state, "overhead", "flow_rules_installed"),
        ),
        "meters_added": _as_float(
            ryu_response.get("meters_added"),
            ryu_body.get("meters_added"),
            state_summary.get("meters_added"),
            _nested_get(next_state, "overhead", "meters_added"),
        ),
        "controller_apply_ms": _as_float(
            ryu_response.get("apply_duration_ms"),
            ryu_body.get("ryu_apply_duration_ms"),
            _nested_get(next_state, "overhead", "controller_apply_ms"),
        ),
        "flow_delete_commands": _as_float(
            ryu_response.get("flow_delete_commands"),
            ryu_body.get("flow_delete_commands"),
            _nested_get(next_state, "overhead", "flow_delete_commands"),
        ),
        "total_cpu_seconds": _as_float(_nested_get(next_state, "overhead", "total_cpu_seconds")),
        "total_memory_kb": _as_float(_nested_get(next_state, "overhead", "total_memory_kb")),
        "active_policy_actions_delta": controller_delta["active_policy_actions_delta"],
        "flow_rules_installed_delta": controller_delta["flow_rules_installed_delta"],
        "meters_added_delta": controller_delta["meters_added_delta"],
        "controller_apply_ms_delta": controller_delta["controller_apply_ms_delta"],
        "flow_delete_commands_delta": controller_delta["flow_delete_commands_delta"],
        "total_cpu_seconds_delta": controller_delta["total_cpu_seconds_delta"],
        "total_memory_kb_delta": controller_delta["total_memory_kb_delta"],
        "llm_provider": llm.get("provider") or trace_metrics.get("llm_trace_provider"),
        "llm_model": llm.get("model") or trace_metrics.get("llm_trace_model"),
        "llm_reasoning_summary": llm.get("reasoning_summary", ""),
        "raw_llm_reasoning_summary": str(
            row.get("raw_llm_reasoning_summary")
            or llm.get("raw_llm_reasoning_summary")
            or ""
        ),
        "executed_decision_reasoning_summary": str(
            row.get("executed_decision_reasoning_summary")
            or llm.get("executed_decision_reasoning_summary")
            or llm.get("reasoning_summary")
            or ""
        ),
        "llm_confidence": _as_float(llm.get("confidence")),
        "llm_parse_success": _coerce_optional_bool(llm.get("parse_success"), stage_validation.get("llm_parse_success")),
        "llm_fallback_used": _coerce_optional_bool(
            llm.get("fallback_used"),
            "llm_fallback_used" in list(stage_validation.get("invalidity_reasons", []) or []),
        ),
        "llm_recovery_used": _coerce_optional_bool(llm.get("recovery_used"), stage_validation.get("llm_recovery_used")),
        "llm_request_success": _coerce_optional_bool(llm.get("request_success"), stage_validation.get("llm_request_success")),
        "llm_request_error": llm.get("request_error") or stage_validation.get("llm_request_error") or "",
        "llm_timeout_failed": bool("timed out" in str(llm.get("request_error") or stage_validation.get("llm_request_error") or "").lower()),
        "llm_latency_ms": trace_metrics.get("llm_latency_ms"),
        "llm_retries_used": trace_metrics.get("llm_retries_used"),
        "baseline_top_defender_strategy_id": baseline_top_strategy_id,
        "baseline_top_defender_utility": _as_float(
            canonical_baseline.get("utility") if canonical_baseline else None,
            row.get("baseline_top_defender_utility"),
            llm.get("baseline_top_defender_utility"),
            _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "utility"),
            _nested_get(row, "baseline_game_prior", "defender", "utility"),
        ),
        "baseline_top_defender_population_share": _as_float(
            canonical_baseline.get("population_share") if canonical_baseline else None,
            row.get("baseline_top_defender_population_share"),
            llm.get("baseline_top_defender_population_share"),
            _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "population_share"),
            _nested_get(row, "baseline_game_prior", "defender", "population_after"),
            _nested_get(row, "baseline_game_prior", "defender", "population"),
        ),
        "baseline_top_defender_tiebreak_reason": str(
            (canonical_baseline.get("tiebreak_reason") if canonical_baseline else "")
            or row.get("baseline_top_defender_tiebreak_reason")
            or llm.get("baseline_top_defender_tiebreak_reason")
            or _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "tiebreak_reason")
            or ""
        ),
        "llm_selected_defender_strategy_id": llm_selected_strategy_id,
        "executed_defender_strategy_id": final_defender_strategy_id,
        "llm_ranked_candidates": llm_ranked_candidates,
        "llm_baseline_alignment": baseline_alignment,
        "raw_llm_alignment": alignment_fields["raw_llm_alignment"],
        "final_executed_alignment": alignment_fields["final_executed_alignment"],
        "llm_override_reason": override_reason,
        "executed_via_fallback": _coerce_optional_bool(
            row.get("executed_via_fallback"),
            llm.get("executed_via_fallback"),
            False,
        ),
        "fallback_reason": str(
            row.get("fallback_reason")
            or llm.get("fallback_reason")
            or _nested_get(row, "fallback_resolution", "fallback_reason")
            or ""
        ),
        "llm_urgency_level": str(
            row.get("llm_urgency_level")
            or llm.get("llm_urgency_level")
            or selection_defender.get("urgency_level")
            or ""
        ),
        "llm_decision_mode": str(
            row.get("llm_decision_mode")
            or llm.get("llm_decision_mode")
            or llm.get("decision_mode")
            or selection_defender.get("decision_mode")
            or ""
        ),
        "llm_telemetry_confidence": str(
            row.get("llm_telemetry_confidence")
            or llm.get("llm_telemetry_confidence")
            or llm.get("telemetry_confidence")
            or selection_defender.get("telemetry_confidence")
            or ""
        ),
        "llm_repeat_previous_action": _coerce_optional_bool(
            row.get("llm_repeat_previous_action"),
            llm.get("llm_repeat_previous_action"),
            llm.get("repeat_previous_action"),
            selection_defender.get("repeat_previous_action"),
            False,
        ),
        "llm_why_not_observe": str(row.get("llm_why_not_observe") or llm.get("llm_why_not_observe") or llm.get("why_not_observe") or ""),
        "llm_why_not_rate_limit": str(row.get("llm_why_not_rate_limit") or llm.get("llm_why_not_rate_limit") or llm.get("why_not_rate_limit") or ""),
        "llm_why_not_quarantine": str(row.get("llm_why_not_quarantine") or llm.get("llm_why_not_quarantine") or llm.get("why_not_quarantine") or ""),
        "llm_expected_security_gain": _as_float(
            row.get("llm_expected_security_gain"),
            llm.get("expected_security_gain"),
            selection_defender.get("expected_security_gain"),
        ),
        "llm_expected_qos_impact": _as_float(
            row.get("llm_expected_qos_impact"),
            llm.get("expected_qos_impact"),
            selection_defender.get("expected_qos_impact"),
        ),
        "llm_expected_controller_cost": _as_float(
            row.get("llm_expected_controller_cost"),
            llm.get("expected_controller_cost"),
            selection_defender.get("expected_controller_cost"),
        ),
        "llm_stage_memory_used": _coerce_optional_bool(
            row.get("llm_stage_memory_used"),
            llm.get("llm_stage_memory_used"),
            selection_defender.get("stage_memory_used"),
        ),
        "fallback_resolution": row.get("fallback_resolution") or stage_summary.get("fallback_resolution") or {},
        "raw_state_effects": stage_validation.get("raw_state_effects", {}) or _nested_get(next_state, "raw_state_effects") or {},
        "normalized_state_effects": stage_validation.get("normalized_state_effects", {}) or _nested_get(next_state, "normalized_state_effects") or {},
        "effect_resolution_policy": str(
            stage_validation.get("effect_resolution_policy")
            or next_state.get("effect_resolution_policy")
            or ""
        ),
        "cloud_policy_observe_only_normalized": _coerce_optional_bool(
            _nested_get(execution_defender, "cloud_policy_context", "json", "cloud_policy_observe_only_normalized"),
            _nested_get(execution_defender, "cloud_policy_decision", "json", "cloud_policy_observe_only_normalized"),
        ),
        "summary_text": normalize_summary_text(stage_summary.get("summary_text")),
    }


def _flatten_decision_row(
    row: dict[str, Any],
    *,
    method: str,
    trace_index: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    scenario_id = _scenario_id_from_row(row)
    stage_id = _as_int(row.get("stage_id"))
    selection_attacker = _nested_get(row, "selection", "attacker", default={}) or {}
    selection_defender = _nested_get(row, "selection", "defender", default={}) or {}
    execution_attacker = _nested_get(row, "execution", "attacker", default={}) or {}
    execution_defender = _nested_get(row, "execution", "defender", default={}) or {}
    stage_summary = row.get("stage_summary", {}) or {}
    stage_validation = _merge_dicts(stage_summary.get("stage_validation", {}), row.get("stage_validation", {}))
    state_summary = row.get("state_summary", {}) or {}
    llm = _merge_dicts(stage_summary.get("llm", {}), row.get("llm", {}))
    trace_metrics = trace_index.get((scenario_id, stage_id or -1), {})
    raw_llm_selected_strategy_id = str(
        row.get("raw_llm_selected_defender_strategy_id")
        or llm.get("raw_llm_selected_defender_strategy_id")
        or row.get("llm_selected_defender_strategy_id")
        or llm.get("llm_selected_defender_strategy_id")
        or selection_defender.get("id")
        or ""
    )
    final_defender_strategy_id = str(
        row.get("final_defender_strategy_id")
        or llm.get("final_defender_strategy_id")
        or selection_defender.get("id")
        or ""
    )
    baseline_top_strategy_id = str(
        row.get("baseline_top_defender_strategy_id")
        or llm.get("baseline_top_defender_strategy_id")
        or _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "strategy_id")
        or _nested_get(row, "baseline_game_prior", "defender", "id")
        or ""
    )
    alignment_fields = _canonical_alignment_fields(
        baseline_top_defender_strategy_id=baseline_top_strategy_id,
        raw_llm_selected_defender_strategy_id=raw_llm_selected_strategy_id,
        final_defender_strategy_id=final_defender_strategy_id,
    )
    return {
        "method": method,
        "scenario_id": scenario_id,
        "stage_id": stage_id,
        "recorded_at": row.get("recorded_at"),
        "decision_source": row.get("decision_source", "game_baseline"),
        "attacker_strategy_id": selection_attacker.get("id"),
        "defender_strategy_id": selection_defender.get("id"),
        "attacker_execution_status": execution_attacker.get("status", ""),
        "defender_execution_status": execution_defender.get("status", ""),
        "attack_active": _coerce_bool(state_summary.get("attack_active"), False),
        "attack_effect_success": _coerce_bool(state_summary.get("attack_effect_success"), False),
        "defense_success": _coerce_bool(state_summary.get("defense_success"), False),
        "path_stage": _as_float(state_summary.get("path_stage")),
        "llm_provider": llm.get("provider") or trace_metrics.get("llm_trace_provider"),
        "llm_model": llm.get("model") or trace_metrics.get("llm_trace_model"),
        "llm_reasoning_summary": llm.get("reasoning_summary", ""),
        "raw_llm_reasoning_summary": str(llm.get("raw_llm_reasoning_summary") or ""),
        "executed_decision_reasoning_summary": str(
            row.get("executed_decision_reasoning_summary")
            or llm.get("executed_decision_reasoning_summary")
            or llm.get("reasoning_summary")
            or ""
        ),
        "llm_parse_success": _coerce_optional_bool(llm.get("parse_success"), stage_validation.get("llm_parse_success")),
        "llm_fallback_used": _coerce_optional_bool(
            llm.get("fallback_used"),
            "llm_fallback_used" in list(stage_validation.get("invalidity_reasons", []) or []),
        ),
        "llm_recovery_used": _coerce_optional_bool(llm.get("recovery_used"), stage_validation.get("llm_recovery_used")),
        "llm_latency_ms": trace_metrics.get("llm_latency_ms"),
        "baseline_top_defender_strategy_id": baseline_top_strategy_id,
        "baseline_top_defender_population_share": _as_float(
            row.get("baseline_top_defender_population_share"),
            llm.get("baseline_top_defender_population_share"),
            _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "population_share"),
        ),
        "baseline_top_defender_tiebreak_reason": str(
            row.get("baseline_top_defender_tiebreak_reason")
            or llm.get("baseline_top_defender_tiebreak_reason")
            or _nested_get(row, "baseline_game_prior", "defender", "canonical_baseline_top", "tiebreak_reason")
            or ""
        ),
        "llm_selected_defender_strategy_id": raw_llm_selected_strategy_id,
        "raw_llm_selected_defender_strategy_id": raw_llm_selected_strategy_id,
        "final_defender_strategy_id": final_defender_strategy_id,
        "llm_baseline_alignment": alignment_fields["llm_baseline_alignment"],
        "raw_llm_alignment": alignment_fields["raw_llm_alignment"],
        "final_executed_alignment": alignment_fields["final_executed_alignment"],
        "llm_override_reason": str(
            row.get("llm_override_reason")
            or llm.get("llm_override_reason")
            or ""
        ),
        "llm_decision_mode": str(row.get("llm_decision_mode") or llm.get("llm_decision_mode") or llm.get("decision_mode") or ""),
        "llm_telemetry_confidence": str(row.get("llm_telemetry_confidence") or llm.get("llm_telemetry_confidence") or llm.get("telemetry_confidence") or ""),
        "llm_repeat_previous_action": _coerce_optional_bool(row.get("llm_repeat_previous_action"), llm.get("llm_repeat_previous_action"), llm.get("repeat_previous_action"), False),
        "llm_ranked_candidates": (
            row.get("llm_ranked_candidates")
            or llm.get("llm_ranked_candidates")
            or []
        ),
        "executed_via_fallback": _coerce_optional_bool(
            row.get("executed_via_fallback"),
            llm.get("executed_via_fallback"),
            False,
        ),
        "fallback_reason": str(
            row.get("fallback_reason")
            or llm.get("fallback_reason")
            or _nested_get(row, "fallback_resolution", "fallback_reason")
            or ""
        ),
        "summary_text": normalize_summary_text(stage_summary.get("summary_text")),
    }


def _flatten_summary_row(row: dict[str, Any], *, method: str) -> dict[str, Any]:
    execution = row.get("execution", {}) or {}
    stage_validation = row.get("stage_validation", {}) or {}
    llm = row.get("llm", {}) or {}
    return {
        "method": method,
        "scenario_id": row.get("scenario_id"),
        "stage_id": _as_int(row.get("stage_id")),
        "recorded_at": row.get("recorded_at"),
        "decision_source": row.get("decision_source", "game_baseline"),
        "attacker_strategy_id": row.get("attacker_strategy_id"),
        "defender_strategy_id": row.get("defender_strategy_id"),
        "defender_action": execution.get("defender_action"),
        "defender_target": execution.get("defender_target"),
        "defender_execution_status": execution.get("defender_status"),
        "defense_executed": _coerce_bool(execution.get("defense_executed"), execution.get("defender_status") == "executed"),
        "defense_confirmed": _coerce_bool(execution.get("defense_confirmed"), stage_validation.get("defense_confirmed"), False),
        "defense_effects_confirmed": _coerce_bool(
            execution.get("defense_effects_confirmed"),
            stage_validation.get("defense_effects_confirmed"),
            False,
        ),
        "defense_applied_but_not_effective": _coerce_bool(
            execution.get("defense_applied_but_not_effective"),
            stage_validation.get("defense_applied_but_not_effective"),
            False,
        ),
        "stage_valid": _coerce_bool(execution.get("stage_valid"), stage_validation.get("llm_stage_valid"), False),
        "stage_success": _coerce_bool(execution.get("stage_success"), stage_validation.get("stage_success"), False),
        "llm_provider": llm.get("provider"),
        "llm_model": llm.get("model"),
        "llm_reasoning_summary": llm.get("reasoning_summary", ""),
        "raw_llm_reasoning_summary": llm.get("raw_llm_reasoning_summary", ""),
        "executed_decision_reasoning_summary": llm.get("executed_decision_reasoning_summary", llm.get("reasoning_summary", "")),
        "llm_decision_mode": llm.get("llm_decision_mode") or llm.get("decision_mode") or "",
        "llm_telemetry_confidence": llm.get("llm_telemetry_confidence") or llm.get("telemetry_confidence") or "",
        "llm_repeat_previous_action": _coerce_optional_bool(llm.get("llm_repeat_previous_action"), llm.get("repeat_previous_action"), False),
        "llm_fallback_used": _coerce_optional_bool(
            llm.get("fallback_used"),
            "llm_fallback_used" in list(stage_validation.get("invalidity_reasons", []) or []),
        ),
        "raw_llm_selected_defender_strategy_id": str(
            llm.get("raw_llm_selected_defender_strategy_id")
            or row.get("raw_llm_selected_defender_strategy_id")
            or row.get("defender_strategy_id")
            or ""
        ),
        "final_defender_strategy_id": str(
            llm.get("final_defender_strategy_id")
            or row.get("final_defender_strategy_id")
            or row.get("defender_strategy_id")
            or ""
        ),
        "executed_via_fallback": _coerce_optional_bool(llm.get("executed_via_fallback"), False),
        "fallback_reason": str(llm.get("fallback_reason") or _nested_get(row, "fallback_resolution", "fallback_reason") or ""),
        "sensor_to_edge_latency_delta_ms": _as_float(_nested_get(row, "qos_delta", "sensor_to_edge_latency_ms")),
        "edge_to_cloud_latency_delta_ms": _as_float(_nested_get(row, "qos_delta", "edge_to_cloud_latency_ms")),
        "throughput_delta_bps": _as_float(_nested_get(row, "qos_delta", "throughput_bytes_per_second")),
        "flow_rules_installed_delta": _as_float(
            _nested_get(row, "controller_delta", "flow_rules_installed_delta"),
            _nested_get(row, "controller_delta", "flow_rules_installed"),
        ),
        "meters_added_delta": _as_float(
            _nested_get(row, "controller_delta", "meters_added_delta"),
            _nested_get(row, "controller_delta", "meters_added"),
        ),
        "controller_apply_ms_delta": _as_float(
            _nested_get(row, "controller_delta", "controller_apply_ms_delta"),
            _nested_get(row, "controller_delta", "apply_ms"),
        ),
        "summary_text": normalize_summary_text(row.get("summary_text")),
    }


def _flatten_population_state(payload: dict[str, Any], *, method: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    scenario_id = _nested_get(payload, "last_stage", "scenario_id")
    selected_attacker = _nested_get(payload, "last_stage", "selected_attacker")
    selected_defender = _nested_get(payload, "last_stage", "selected_defender")
    rows: list[dict[str, Any]] = []
    for role in ("attacker", "defender"):
        population = payload.get(role, {}) or {}
        for strategy_id, share in population.items():
            rows.append(
                {
                    "method": method,
                    "scenario_id": scenario_id,
                    "role": role,
                    "strategy_id": strategy_id,
                    "population_share": _as_float(share),
                    "selected_last_stage": strategy_id == (selected_attacker if role == "attacker" else selected_defender),
                    "last_stage_path_stage": _as_float(_nested_get(payload, "last_stage", "path_stage")),
                }
            )
    return rows


def _filter_by_scenario(frame: pd.DataFrame, scenario_ids: set[str]) -> pd.DataFrame:
    if frame.empty or "scenario_id" not in frame.columns:
        return frame
    return frame.loc[frame["scenario_id"].isin(scenario_ids)].copy()


def _scenario_id_from_row(row: dict[str, Any]) -> str:
    return str(
        row.get("scenario_id")
        or _nested_get(row, "stage_summary", "scenario_id")
        or _nested_get(row, "state_summary", "scenario_id")
        or _nested_get(row, "selection", "attacker", "scenario_id")
        or _nested_get(row, "selection", "defender", "scenario_id")
        or ""
    )


def _normalize_qos_delta(stage_summary_delta: dict[str, Any], previous_state: dict[str, Any], next_state: dict[str, Any]) -> dict[str, float | None]:
    previous_qos = previous_state.get("qos", {}) or {}
    next_qos = next_state.get("qos", {}) or {}
    return {
        "sensor_to_edge_latency_delta_ms": _as_float(
            stage_summary_delta.get("sensor_to_edge_latency_ms"),
            _diff(next_qos.get("sensor_to_edge_latency_ms"), previous_qos.get("sensor_to_edge_latency_ms")),
        ),
        "edge_to_cloud_latency_delta_ms": _as_float(
            stage_summary_delta.get("edge_to_cloud_latency_ms"),
            _diff(next_qos.get("edge_to_cloud_latency_ms"), previous_qos.get("edge_to_cloud_latency_ms")),
        ),
        "throughput_delta_bps": _as_float(
            stage_summary_delta.get("throughput_bytes_per_second"),
            _diff(next_qos.get("throughput_bytes_per_second"), previous_qos.get("throughput_bytes_per_second")),
        ),
        "loss_rate_delta": _as_float(
            stage_summary_delta.get("loss_rate"),
            _diff(next_qos.get("loss_rate"), previous_qos.get("loss_rate")),
        ),
    }


def _normalize_controller_delta(stage_controller_delta: dict[str, Any], previous_state: dict[str, Any], next_state: dict[str, Any]) -> dict[str, float | None]:
    previous_overhead = previous_state.get("overhead", {}) or {}
    next_overhead = next_state.get("overhead", {}) or {}
    delta_map = {
        "active_policy_actions_delta": ("active_policy_actions_delta", "active_actions", "controller_active_actions"),
        "flow_rules_installed_delta": ("flow_rules_installed_delta", "flow_rules_installed", "flow_rules_installed"),
        "meters_added_delta": ("meters_added_delta", "meters_added", "meters_added"),
        "controller_apply_ms_delta": ("controller_apply_ms_delta", "apply_ms", "controller_apply_ms"),
        "flow_delete_commands_delta": ("flow_delete_commands_delta", "flow_delete_commands", "flow_delete_commands"),
        "total_cpu_seconds_delta": ("total_cpu_seconds_delta", "total_cpu_seconds", "total_cpu_seconds"),
        "total_memory_kb_delta": ("total_memory_kb_delta", "total_memory_kb", "total_memory_kb"),
    }
    output: dict[str, float | None] = {}
    for output_name, keys in delta_map.items():
        output[output_name] = _as_float(
            *[stage_controller_delta.get(key) for key in keys[:-1]],
            _diff(next_overhead.get(keys[-1]), previous_overhead.get(keys[-1])),
        )
    return output


def _infer_stage_kind(attacker_status: str, defender_status: str) -> str:
    if attacker_status == "dry_run" or defender_status == "dry_run":
        return "dry_run"
    if attacker_status == "no_active_attacker_strategy":
        return "warmup"
    return "experimental"


def _parse_json_body(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _any_source_true(details: dict[str, Any], field: str) -> bool:
    sources = details.get(field, {}) if isinstance(details, dict) else {}
    if not isinstance(sources, dict):
        return _coerce_bool(sources)
    return any(_coerce_bool(value) for value in sources.values())


def _report_target_is_gateway(target: str) -> bool:
    normalized = str(target or "").lower()
    return normalized.endswith("_gw") or "gateway" in normalized or normalized.endswith("_gateway")


def _report_target_is_worker(target: str) -> bool:
    normalized = str(target or "").lower()
    return any(token in normalized for token in ("worker", "_vm_", "vm_", "_svc", "service"))


def _normalize_semantic_effects_for_report(
    *,
    expected_effects: list[Any],
    stage_validation: dict[str, Any],
    defender_action: Any,
    defender_target: Any,
    previous_state: dict[str, Any],
    next_state: dict[str, Any],
) -> dict[str, Any]:
    expected = [str(effect) for effect in expected_effects if str(effect)]
    has_semantic_fields = (
        "semantic_observed_defense_effects" in stage_validation
        or "semantic_missing_defense_effects" in stage_validation
    )
    existing_observed = list(stage_validation.get("semantic_observed_defense_effects", []) or [])
    existing_missing = list(stage_validation.get("semantic_missing_defense_effects", []) or [])
    existing_details = stage_validation.get("semantic_effect_mapping_details", {}) or {}
    if has_semantic_fields and (existing_observed or (expected and len(existing_missing) < len(expected))):
        return {
            "observed": [str(item) for item in existing_observed],
            "missing": [str(item) for item in existing_missing],
            "details": existing_details,
        }

    source_details = stage_validation.get("effect_source_details", {}) or {}
    target = str(defender_target or "")
    action = str(defender_action or "")
    previous_path_stage = _as_int(previous_state.get("path_stage")) or 0
    next_path_stage = _as_int(next_state.get("path_stage")) or 0
    drop_rules_active = _any_source_true(source_details, "drop_rules_active") or _coerce_bool(next_state.get("drop_rules_active"))
    counters_stopped = _any_source_true(source_details, "counters_stopped") or _coerce_bool(next_state.get("counters_stopped"))
    rate_limit_active = _any_source_true(source_details, "rate_limit_active") or _coerce_bool(next_state.get("rate_limit_active"))
    traffic_blocking = bool(drop_rules_active or counters_stopped)
    observed: list[str] = []
    details: dict[str, dict[str, Any]] = {}
    for effect in expected:
        if effect == "gateway_blocked":
            value = bool(_report_target_is_gateway(target) and (traffic_blocking or action in {"quarantine_sensor", "isolate_sensor"}))
        elif effect == "worker_blocked":
            value = bool(_report_target_is_worker(target) and traffic_blocking)
        elif effect == "path_broken":
            value = bool(next_path_stage < previous_path_stage or not _coerce_bool(next_state.get("attack_active")) or traffic_blocking)
        elif effect == "cloud_progression_suppressed":
            value = bool(
                (_coerce_bool(previous_state.get("cloud_seen")) and not _coerce_bool(next_state.get("cloud_seen")))
                or not _coerce_bool(next_state.get("attack_effect_success"))
                or next_path_stage <= previous_path_stage
            )
        elif effect == "strategy_context_recorded":
            value = True
        elif effect == "drop_rules_active":
            value = drop_rules_active
        elif effect == "counters_stopped":
            value = counters_stopped
        elif effect in {"rate_limit_active", "throttled_traffic"}:
            value = rate_limit_active
        else:
            value = _coerce_bool(next_state.get(effect))
        details[effect] = {
            "observed": value,
            "target": target,
            "action": action,
            "drop_rules_active": drop_rules_active,
            "counters_stopped": counters_stopped,
            "rate_limit_active": rate_limit_active,
            "path_stage_before": previous_path_stage,
            "path_stage_after": next_path_stage,
            "report_backfilled": True,
        }
        if value:
            observed.append(effect)
    return {
        "observed": observed,
        "missing": [effect for effect in expected if effect not in observed],
        "details": details,
    }


def _baseline_candidate_sort_key(item: dict[str, Any]) -> tuple[float, float, float, str]:
    strategy_id = str(item.get("strategy_id") or item.get("id") or "")
    return (
        -float(_as_float(item.get("baseline_utility_prior"), 0.0) or 0.0),
        -float(_as_float(item.get("defender_population_share"), 0.0) or 0.0),
        float(_as_float(item.get("base_cost"), 0.0) or 0.0),
        strategy_id,
    )


def _baseline_tiebreak_reason_from_candidates(chosen: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    chosen_utility = float(_as_float(chosen.get("baseline_utility_prior"), 0.0) or 0.0)
    utility_tied = [
        item for item in candidates
        if float(_as_float(item.get("baseline_utility_prior"), 0.0) or 0.0) == chosen_utility
    ]
    if len(utility_tied) == 1:
        return "highest_utility"
    chosen_population = float(_as_float(chosen.get("defender_population_share"), 0.0) or 0.0)
    population_tied = [
        item for item in utility_tied
        if float(_as_float(item.get("defender_population_share"), 0.0) or 0.0) == chosen_population
    ]
    if len(population_tied) == 1:
        return "utility_tie_highest_population_share"
    chosen_cost = float(_as_float(chosen.get("base_cost"), 0.0) or 0.0)
    cost_tied = [
        item for item in population_tied
        if float(_as_float(item.get("base_cost"), 0.0) or 0.0) == chosen_cost
    ]
    if len(cost_tied) == 1:
        return "utility_population_tie_lowest_base_cost"
    return "utility_population_cost_tie_lexicographic_strategy_id"


def _canonical_baseline_from_ranked_candidates(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    candidates = [item for item in value if isinstance(item, dict) and str(item.get("strategy_id") or item.get("id") or "")]
    if not candidates:
        return {}
    chosen = sorted(candidates, key=_baseline_candidate_sort_key)[0]
    strategy_id = str(chosen.get("strategy_id") or chosen.get("id") or "")
    return {
        "strategy_id": strategy_id,
        "utility": _as_float(chosen.get("baseline_utility_prior"), 0.0),
        "population_share": _as_float(chosen.get("defender_population_share"), 0.0),
        "base_cost": _as_float(chosen.get("base_cost"), 0.0),
        "tiebreak_reason": _baseline_tiebreak_reason_from_candidates(chosen, candidates),
    }


def _nested_get(data: dict[str, Any], *path: str, default: Any = None) -> Any:
    cursor: Any = data
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            return default
        cursor = cursor[key]
    return cursor


def _merge_dicts(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _diff(after: Any, before: Any) -> float | None:
    after_value = _as_float(after)
    before_value = _as_float(before)
    if after_value is None or before_value is None:
        return None
    return after_value - before_value


def _as_int(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_float(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_bool(*values: Any) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n", ""}:
                return False
    return False


def _coerce_optional_bool(*values: Any) -> bool | None:
    seen = False
    for value in values:
        if value is None:
            continue
        seen = True
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
    return None if not seen else False
