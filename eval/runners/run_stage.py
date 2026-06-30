from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from eval.emo_bridge import load_emo_strategy_modules, strategy_dir
from defender.decision.defender_selector import select_baseline_top_defender, select_defender_strategy
from defender.decision.stage_summarizer import build_stage_summary_record
from eval.reports.export_json import write_json
from eval.settings import ResolvedConfig


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_run_id(model_name: str, scenario_id: str) -> str:
    digest = hashlib.sha1(f"{model_name}:{scenario_id}:{_utc_now_iso()}".encode("utf-8")).hexdigest()
    return f"stage_{digest[:12]}"


def _resolve_path(project_root: Path, value: Path | str | None, fallback: Path) -> Path:
    if value in (None, ""):
        return fallback
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


def _next_stage_id(path: Path) -> int:
    if not path.exists():
        return 1
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle) + 1


def _append_jsonl(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _choice(value: Any, configured: Any) -> Any:
    return configured if value is None else value


def _read_latest_jsonl_row(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def consecutive_stage_kind_count(path: Path, scenario_id: str, stage_kind: str) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if str(row.get("scenario_id") or "") != str(scenario_id):
            continue
        kind = str((row.get("stage_validation") or {}).get("stage_kind") or "")
        if kind != stage_kind:
            break
        count += 1
    return count


def stage_validity_counts(path: Path, scenario_id: str) -> tuple[int, int]:
    total = 0
    valid = 0
    if not path.exists():
        return total, valid
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if str(row.get("scenario_id") or "") != str(scenario_id):
            continue
        total += 1
        valid += int(bool((row.get("stage_validation") or {}).get("paper_valid_stage")))
    return total, valid


def _read_latest_stage_memory(
    path: Path,
    scenario_id: str,
    active_defender_ids: set[str] | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row_scenario_id = str(
            row.get("scenario_id")
            or ((row.get("stage_summary") or {}).get("scenario_id"))
            or ((row.get("state_summary") or {}).get("scenario_id"))
            or ""
        )
        if row_scenario_id != scenario_id:
            continue
        stage_summary = row.get("stage_summary") or {}
        stage_validation = row.get("stage_validation") or stage_summary.get("stage_validation") or {}
        selection = row.get("selection") or {}
        previous_state = row.get("previous_state") or {}
        next_state = row.get("next_state") or {}
        execution = row.get("execution") or {}
        raw_previous_defender_strategy_id = str((selection.get("defender") or {}).get("id") or "")
        # If the stored defender strategy ID is not in the current active set, annotate it as
        # stale so the LLM prompt does not carry a hallucination-inducing inactive ID.
        if active_defender_ids is not None and raw_previous_defender_strategy_id and raw_previous_defender_strategy_id not in active_defender_ids:
            previous_defender_strategy_id = ""
            previous_defender_strategy_id_note = (
                f"[stale: '{raw_previous_defender_strategy_id}' was used in a prior stage "
                f"but is NOT active at the current path_stage — do NOT reuse it]"
            )
        else:
            previous_defender_strategy_id = raw_previous_defender_strategy_id
            previous_defender_strategy_id_note = ""
        return {
            "previous_stage_id": row.get("stage_id"),
            "previous_attacker_strategy_id": ((selection.get("attacker") or {}).get("id") or ""),
            "previous_defender_strategy_id": previous_defender_strategy_id,
            "previous_defender_strategy_id_note": previous_defender_strategy_id_note,
            "previous_defender_execution_status": (
                stage_validation.get("defender_execution_status")
                or ((execution.get("defender") or {}).get("status") or "")
            ),
            "previous_defense_confirmed": stage_validation.get("defense_confirmed"),
            "previous_defense_success": (
                (row.get("state_summary") or {}).get("defense_success")
                or ((stage_summary.get("security_outcome") or {}).get("defense_success"))
            ),
            "previous_attack_effect_success": (
                (row.get("state_summary") or {}).get("attack_effect_success")
                or ((stage_summary.get("security_outcome") or {}).get("attack_effect_success"))
            ),
            "previous_stage_success": stage_validation.get("stage_success"),
            "previous_attack_progression_continued": bool(
                ((next_state or {}).get("attack_active"))
                or ((next_state or {}).get("attack_effect_success"))
                or (_safe_int((next_state or {}).get("path_stage")) > _safe_int((previous_state or {}).get("path_stage")))
            ),
            "recent_qos_deltas": dict(stage_summary.get("qos_delta") or {}),
            "recent_controller_deltas": dict(stage_summary.get("controller_delta") or {}),
        }
    return {}


def _compact_active_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": strategy.get("id", ""),
        "name": strategy.get("name", ""),
        "scenario_id": strategy.get("scenario_id", ""),
        "action": strategy.get("action") or strategy.get("live_attack_type") or "",
        "target": strategy.get("target") or strategy.get("target_asset") or "",
        "base_cost": strategy.get("base_cost"),
        "base_reward": strategy.get("base_reward"),
        "expected_effects": strategy.get("expected_effects", []) or [],
    }


def enrich_candidate_rankings(
    rankings: list[dict[str, Any]],
    active_defenders: list[dict[str, Any]],
    game: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {str(strategy.get("id") or ""): strategy for strategy in active_defenders}
    enriched = []
    for index, candidate in enumerate(rankings or [], start=1):
        strategy_id = str(candidate.get("strategy_id") or candidate.get("id") or "")
        strategy = by_id.get(strategy_id, {})
        defense_cost = strategy.get("defense_cost") or {}
        security_gain = _safe_float(
            candidate.get("expected_security_gain"),
            _safe_float(strategy.get("base_reward")),
        )
        qos_impact = _safe_float(
            candidate.get("expected_qos_impact"),
            _safe_float(defense_cost.get("negative_service_impact")),
        )
        controller_cost = _safe_float(
            candidate.get("expected_controller_cost"),
            _safe_float(strategy.get("base_cost")),
        )
        enriched.append({
            **candidate,
            "id": strategy_id,
            "strategy_id": strategy_id,
            "rank": _safe_int(candidate.get("llm_rank"), index),
            "utility": _safe_float((game.get("defender_utilities") or {}).get(strategy_id)),
            "expected_security_gain": security_gain,
            "expected_qos_impact": qos_impact,
            "expected_controller_cost": controller_cost,
            "expected_tradeoff": security_gain - qos_impact - controller_cost,
        })
    return enriched


def _parse_json_body(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        body = value.get("body")
        if isinstance(value.get("json"), dict):
            return value["json"]
        if isinstance(body, str):
            try:
                parsed = json.loads(body or "{}")
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "installed", "active"}:
            return True
        if normalized in {"false", "0", "no", "n", "", "none", "inactive"}:
            return False
    return None


def _truth_from_sources(sources: dict[str, Any]) -> tuple[bool, bool, dict[str, bool | None]]:
    normalized = {name: _bool_or_none(value) for name, value in sources.items()}
    known_values = [value for value in normalized.values() if value is not None]
    value = any(known_values)
    conflict = bool(known_values and len(set(known_values)) > 1)
    return value, conflict, normalized


def _attacker_operation_id(attacker_execution: dict[str, Any]) -> str:
    post_body = _parse_json_body((attacker_execution or {}).get("post_result") or {})
    return str(post_body.get("operation_id") or post_body.get("id") or "").strip()


def compute_llm_baseline_alignment(
    *,
    baseline_top_defender_strategy_id: str,
    raw_llm_selected_defender_strategy_id: str,
    final_defender_strategy_id: str,
    executed_via_fallback: bool,
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
        "raw_llm_alignment_vs_baseline": raw_alignment,
        "final_decision_alignment_vs_baseline": final_alignment,
        "llm_baseline_alignment": raw_alignment,
        "raw_llm_baseline_alignment": raw_alignment,
        "final_baseline_alignment": final_alignment,
        "alignment_source": "raw_llm_selected_defender_vs_canonical_baseline_top",
    }


def merge_confirmed_defense_effects_into_state(
    next_state: dict[str, Any] | None,
    effect_validation: dict[str, Any],
) -> dict[str, Any]:
    state = next_state if isinstance(next_state, dict) else {}
    raw_state_effects = {
        "drop_rules_active": bool(state.get("drop_rules_active")),
        "counters_stopped": bool(state.get("counters_stopped")),
        "rate_limit_active": bool(state.get("rate_limit_active")),
    }
    normalized_state_effects = {
        "drop_rules_active": bool((effect_validation.get("reconciled_effects") or {}).get("drop_rules_active")),
        "counters_stopped": bool((effect_validation.get("reconciled_effects") or {}).get("counters_stopped")),
        "rate_limit_active": bool((effect_validation.get("reconciled_effects") or {}).get("rate_limit_active")),
    }
    semantic_observed = {
        str(item) for item in (effect_validation.get("semantic_observed_defense_effects") or []) if str(item)
    }
    effect_source_details = effect_validation.get("effect_source_details") or {}
    state_effect_sources: dict[str, str] = {}
    for effect_name, normalized_value in normalized_state_effects.items():
        source_parts: list[str] = []
        if effect_name in semantic_observed:
            source_parts.append("semantic")
        details = effect_source_details.get(effect_name) or {}
        if details.get("defense_event") is True:
            source_parts.append("defense_event")
        if details.get("ryu_response") is True:
            source_parts.append("ryu")
        if details.get("state") is True:
            source_parts.append("raw_state")
        if normalized_value and not source_parts:
            source_parts.append("reconciled")
        state_effect_sources[effect_name] = "+".join(source_parts)
        state[f"raw_{effect_name}"] = raw_state_effects[effect_name]
        state[f"normalized_{effect_name}"] = normalized_value
        state[effect_name] = normalized_value
    state["raw_state_effects"] = raw_state_effects
    state["normalized_state_effects"] = normalized_state_effects
    state["state_effect_sources"] = state_effect_sources
    state["effect_resolution_policy"] = "semantic_confirmation_preferred_over_stale_raw_state"
    return state


def _build_llm_cloud_policy_context(
    modules: Any,
    state: dict[str, Any],
    selection_pair: dict[str, Any],
    attacker_execution: dict[str, Any],
    defender_result: Any,
) -> dict[str, Any]:
    payload = modules.strategy_runtime.cloud_policy_context_payload(
        state,
        selection_pair,
        attacker_execution=attacker_execution,
    )
    mulval = state.get("mulval") or {}
    plausible_paths = mulval.get("plausible_paths") or []
    llm_context = {
        "decision_source": "llm_defender",
        "selected_defender_strategy_id": ((selection_pair.get("defender") or {}).get("id") or ""),
        "raw_llm_selected_defender_strategy_id": getattr(defender_result, "raw_selected_strategy_id", ""),
        "final_defender_strategy_id": ((selection_pair.get("defender") or {}).get("id") or ""),
        "llm_provider": defender_result.trace.provider,
        "llm_model": defender_result.trace.model_name,
        "llm_reasoning_summary": ((selection_pair.get("defender") or {}).get("reasoning_summary") or ""),
        "raw_llm_reasoning_summary": getattr(defender_result, "raw_reasoning_summary", ""),
        "llm_fallback_used": bool(defender_result.fallback_used),
        "llm_fallback_reason": defender_result.fallback_reason,
        "llm_fallback_constraint_name": getattr(defender_result, "fallback_constraint_name", ""),
        "llm_baseline_alignment": defender_result.baseline_alignment,
        "llm_override_reason": defender_result.override_reason,
        "llm_urgency_level": defender_result.urgency_level,
        "operation_id": _attacker_operation_id(attacker_execution),
        "attack_dispatch_status": (attacker_execution or {}).get("status", ""),
        "caldera_success": (attacker_execution or {}).get("status") == "dispatched",
        "first_mulval_path": plausible_paths[0] if plausible_paths else [],
        "mulval_path_count": len(plausible_paths),
        "selected_attacker_path": ((selection_pair.get("attacker") or {}).get("strategy") or {}).get("path", []) or [],
    }
    payload["llm_defender_selection"] = llm_context
    selected_defender = payload.get("selected_defender_strategy") or {}
    if isinstance(selected_defender, dict):
        selected_defender["decision_source"] = "llm_defender"
        selected_defender["llm_reasoning_summary"] = llm_context["llm_reasoning_summary"]
        selected_defender["raw_llm_reasoning_summary"] = llm_context["raw_llm_reasoning_summary"]
        selected_defender["llm_fallback_used"] = llm_context["llm_fallback_used"]
        selected_defender["llm_fallback_reason"] = llm_context["llm_fallback_reason"]
        selected_defender["raw_llm_selected_defender_strategy_id"] = llm_context["raw_llm_selected_defender_strategy_id"]
        selected_defender["final_defender_strategy_id"] = llm_context["final_defender_strategy_id"]
        payload["selected_defender_strategy"] = selected_defender
    return payload


def _effect_is_observed(
    effect: str,
    previous_state: dict[str, Any],
    next_state: dict[str, Any] | None,
    defender_execution: dict[str, Any],
) -> bool:
    state = next_state or {}
    previous_overhead = previous_state.get("overhead") or {}
    overhead = state.get("overhead") or {}
    payload = defender_execution.get("payload") or {}
    action = str(payload.get("action") or "")
    previous_path_stage = _safe_int(previous_state.get("path_stage"))
    next_path_stage = _safe_int(state.get("path_stage"))

    if effect == "drop_rules_active":
        return bool(state.get("drop_rules_active"))
    if effect == "counters_stopped":
        return bool(state.get("counters_stopped"))
    if effect == "defense_success":
        return bool(state.get("defense_success"))
    if effect == "throttled_traffic":
        return _safe_int(overhead.get("meters_added")) > _safe_int(previous_overhead.get("meters_added"))
    if effect == "lower_gateway_queue":
        return (
            _safe_float(state.get("qos", {}).get("edge_to_cloud_latency_ms"))
            <= _safe_float(previous_state.get("qos", {}).get("edge_to_cloud_latency_ms"))
        )
    if effect == "worker_blocked":
        return bool(state.get("drop_rules_active") or state.get("counters_stopped"))
    if effect == "cloud_progression_suppressed":
        return not bool(state.get("attack_effect_success")) or next_path_stage < previous_path_stage
    if effect == "strategy_context_recorded":
        return True

    if effect.endswith("_active"):
        return bool(state.get(effect))
    if effect.endswith("_success"):
        return bool(state.get(effect))
    if action == "rate_limit" and effect in {"lower_gateway_queue", "throttled_traffic"}:
        return _safe_int(overhead.get("meters_added")) > 0
    return False


def _current_path_nodes(
    previous_state: dict[str, Any],
    next_state: dict[str, Any],
    defender_strategy: dict[str, Any],
) -> set[str]:
    nodes: set[str] = set()
    for value in (
        previous_state.get("current_path"),
        next_state.get("current_path"),
        defender_strategy.get("path"),
        (defender_strategy.get("strategy") or {}).get("path"),
    ):
        if isinstance(value, list):
            nodes.update(str(item) for item in value if item not in ("", None))
    return nodes


def _target_is_gateway(target: str) -> bool:
    normalized = target.lower()
    return normalized.endswith("_gw") or "gateway" in normalized or normalized.endswith("_gateway")


def _target_is_worker(target: str) -> bool:
    normalized = target.lower()
    return any(token in normalized for token in ("worker", "_vm_", "vm_", "_svc", "service"))


def _contains_target(value: Any, target: str) -> bool:
    if not target:
        return False
    if isinstance(value, dict):
        return any(_contains_target(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_target(item, target) for item in value)
    return str(value) == target


def _semantic_effect_observed(
    *,
    effect: str,
    previous_state: dict[str, Any],
    next_state: dict[str, Any],
    defender_execution: dict[str, Any],
    defender_strategy: dict[str, Any],
    reconciled_effects: dict[str, bool],
    active_policy_actions: int,
    flow_rules_installed: int,
    meters_added: int,
    installed_status: bool,
) -> tuple[bool, dict[str, Any]]:
    payload = defender_execution.get("payload") or {}
    action = str(payload.get("action") or defender_strategy.get("action") or "")
    target = str(payload.get("target") or defender_strategy.get("target") or "")
    previous_path_stage = _safe_int(previous_state.get("path_stage"))
    next_path_stage = _safe_int(next_state.get("path_stage"))
    path_nodes = _current_path_nodes(previous_state, next_state, defender_strategy)
    target_on_path = bool(target and target in path_nodes)
    traffic_blocking = bool(
        reconciled_effects.get("drop_rules_active")
        or reconciled_effects.get("counters_stopped")
        or (action in {"quarantine_sensor", "isolate_sensor"} and flow_rules_installed > 0)
    )
    active_action_for_target = bool(
        active_policy_actions > 0
        and (
            not target
            or _contains_target(defender_execution.get("post_result"), target)
            or _contains_target(defender_execution.get("defense_event"), target)
            or _contains_target(next_state.get("mtd_status") or next_state.get("controller_status") or {}, target)
        )
    )
    details = {
        "target": target,
        "action": action,
        "target_is_gateway": _target_is_gateway(target),
        "target_is_worker": _target_is_worker(target),
        "target_on_path": target_on_path,
        "path_stage_before": previous_path_stage,
        "path_stage_after": next_path_stage,
        "traffic_blocking": traffic_blocking,
        "active_action_for_target": active_action_for_target,
        "installed_status": installed_status,
        "flow_rules_installed": flow_rules_installed,
        "meters_added": meters_added,
    }

    if effect in reconciled_effects:
        observed = bool(reconciled_effects[effect])
    elif effect == "gateway_blocked":
        observed = bool(
            _target_is_gateway(target)
            and (installed_status or traffic_blocking or active_action_for_target)
        )
    elif effect == "worker_blocked":
        observed = bool(
            _target_is_worker(target)
            and (installed_status or traffic_blocking or active_action_for_target)
        )
    elif effect == "path_broken":
        observed = bool(
            next_path_stage < previous_path_stage
            or not bool(next_state.get("attack_active"))
            or (target_on_path and traffic_blocking)
            or (_target_is_gateway(target) and traffic_blocking)
        )
    elif effect == "cloud_progression_suppressed":
        observed = bool(
            (bool(previous_state.get("cloud_seen")) and not bool(next_state.get("cloud_seen")))
            or not bool(next_state.get("attack_effect_success"))
            or next_path_stage <= previous_path_stage
        )
    elif effect == "strategy_context_recorded":
        observed = bool(defender_execution.get("status") in {"observe_only", "dry_run", "executed"})
    elif effect == "throttled_traffic":
        observed = bool(reconciled_effects.get("rate_limit_active") or meters_added > 0)
    elif effect == "lower_gateway_queue":
        observed = bool(_effect_is_observed(effect, previous_state, next_state, defender_execution))
    elif effect == "defense_success":
        observed = bool(not next_state.get("attack_effect_success"))
    else:
        observed = bool(_effect_is_observed(effect, previous_state, next_state, defender_execution))
    details["observed"] = observed
    return observed, details


def resolve_defense_effect_observations(
    *,
    expected_effects: list[str],
    previous_state: dict[str, Any],
    next_state: dict[str, Any],
    defender_execution: dict[str, Any],
    defender_strategy: dict[str, Any],
    reconciled_effects: dict[str, bool],
    active_policy_actions: int,
    flow_rules_installed: int,
    meters_added: int,
    installed_status: bool,
) -> dict[str, Any]:
    details: dict[str, dict[str, Any]] = {}
    observed: list[str] = []
    for effect in expected_effects:
        effect_name = str(effect)
        is_observed, effect_details = _semantic_effect_observed(
            effect=effect_name,
            previous_state=previous_state,
            next_state=next_state,
            defender_execution=defender_execution,
            defender_strategy=defender_strategy,
            reconciled_effects=reconciled_effects,
            active_policy_actions=active_policy_actions,
            flow_rules_installed=flow_rules_installed,
            meters_added=meters_added,
            installed_status=installed_status,
        )
        details[effect_name] = effect_details
        if is_observed:
            observed.append(effect_name)
    missing = [str(effect) for effect in expected_effects if str(effect) not in observed]
    return {
        "semantic_observed_defense_effects": observed,
        "semantic_missing_defense_effects": missing,
        "semantic_effect_mapping_details": details,
    }


def reconcile_defense_effects(
    selection_pair: dict[str, Any],
    previous_state: dict[str, Any],
    next_state: dict[str, Any] | None,
    defender_execution: dict[str, Any],
) -> dict[str, Any]:
    defender_selection = selection_pair.get("defender") or {}
    defender_strategy = (defender_selection.get("strategy") or defender_selection) if isinstance(defender_selection, dict) else {}
    expected_effects = (
        (defender_strategy.get("expected_effects", []) if isinstance(defender_strategy, dict) else [])
        or (defender_selection.get("expected_effects", []) if isinstance(defender_selection, dict) else [])
        or []
    )
    next_state = next_state or {}
    payload = defender_execution.get("payload") or {}
    action = str(payload.get("action") or "")
    ryu_body = _parse_json_body(defender_execution.get("post_result") or {})
    defense_event = defender_execution.get("defense_event") if isinstance(defender_execution.get("defense_event"), dict) else {}
    defense_signals = ((defense_event.get("payload") or {}).get("signals") or {})
    overhead = next_state.get("overhead") or {}
    active_policy_actions = max(
        _safe_int(overhead.get("controller_active_actions")),
        _safe_int(defense_signals.get("active_policy_actions")),
        _safe_int(ryu_body.get("active_policy_actions")),
    )
    flow_rules_installed = max(
        _safe_int(overhead.get("flow_rules_installed")),
        _safe_int(defense_signals.get("flow_rules_installed")),
        _safe_int(ryu_body.get("flow_rules_installed")),
    )
    meters_added = max(
        _safe_int(overhead.get("meters_added")),
        _safe_int(defense_signals.get("meters_added")),
        _safe_int(ryu_body.get("meters_added")),
    )
    installed_status = str(ryu_body.get("status", "")).lower() in {"installed", "accepted"}
    isolation_actions = {"quarantine_sensor", "isolate_sensor"}

    effect_sources = {
        "drop_rules_active": {
            "state": next_state.get("drop_rules_active"),
            "defense_event": defense_signals.get("drop_rules_active"),
            "ryu_response": ryu_body.get("drop_rules_active") if "drop_rules_active" in ryu_body else (action in isolation_actions and flow_rules_installed > 0),
        },
        "counters_stopped": {
            "state": next_state.get("counters_stopped"),
            "defense_event": defense_signals.get("counters_stopped"),
            "ryu_response": ryu_body.get("counters_stopped") if "counters_stopped" in ryu_body else (action in isolation_actions and flow_rules_installed > 0),
        },
        "rate_limit_active": {
            "state": next_state.get("rate_limit_active"),
            "defense_event": defense_signals.get("rate_limit_active"),
            "ryu_response": ryu_body.get("rate_limit_active") if "rate_limit_active" in ryu_body else (action == "rate_limit" and meters_added > 0),
        },
    }
    reconciled_effects: dict[str, bool] = {}
    effect_source_details: dict[str, dict[str, bool | None]] = {}
    conflict = False
    for effect, sources in effect_sources.items():
        value, source_conflict, details = _truth_from_sources(sources)
        reconciled_effects[effect] = value
        effect_source_details[effect] = details
        conflict = conflict or source_conflict

    semantic_effects = resolve_defense_effect_observations(
        expected_effects=[str(effect) for effect in expected_effects],
        previous_state=previous_state,
        next_state=next_state,
        defender_execution=defender_execution,
        defender_strategy=defender_strategy if isinstance(defender_strategy, dict) else {},
        reconciled_effects=reconciled_effects,
        active_policy_actions=active_policy_actions,
        flow_rules_installed=flow_rules_installed,
        meters_added=meters_added,
        installed_status=installed_status,
    )
    observed_effects = semantic_effects["semantic_observed_defense_effects"]
    missing_effects = semantic_effects["semantic_missing_defense_effects"]
    defense_confirmed = bool(
        defender_execution.get("status") == "executed"
        and (defender_execution.get("post_result") or {}).get("ok")
        and (
            active_policy_actions > 0
            and (flow_rules_installed > 0 or meters_added > 0 or installed_status)
        )
    )
    return {
        "expected_effects": expected_effects,
        "observed_effects": observed_effects,
        "missing_effects": missing_effects,
        "effects_confirmed": not missing_effects if expected_effects else True,
        **semantic_effects,
        "reconciled_effects": reconciled_effects,
        "defense_confirmed": defense_confirmed,
        "effect_source_conflict": conflict,
        "effect_source_details": effect_source_details,
        "active_policy_actions": active_policy_actions,
        "flow_rules_installed": flow_rules_installed,
        "meters_added": meters_added,
    }


def resolve_security_outcome(
    *,
    previous_state: dict[str, Any],
    next_state: dict[str, Any] | None,
    defender_execution: dict[str, Any],
    effect_validation: dict[str, Any],
) -> dict[str, Any]:
    current_state = next_state or previous_state
    previous_path_stage = _safe_int(previous_state.get("path_stage"))
    next_path_stage = _safe_int(current_state.get("path_stage"))
    attack_effect_success = bool(current_state.get("attack_effect_success"))
    defense_executed = defender_execution.get("status") == "executed"
    defense_confirmed = bool(effect_validation.get("defense_confirmed"))
    defense_effects_confirmed = bool(effect_validation.get("effects_confirmed"))
    attack_deactivated = bool(previous_state.get("attack_active")) and not bool(current_state.get("attack_active"))
    path_regressed = next_path_stage < previous_path_stage
    progression_reduced = bool(path_regressed or attack_deactivated)
    defense_success = bool((not attack_effect_success) or progression_reduced)
    defense_applied_but_not_effective = bool(defense_executed and defense_confirmed and not defense_success)
    return {
        "attack_active": bool(current_state.get("attack_active")),
        "attack_effect_success": attack_effect_success,
        "raw_state_defense_success": bool(current_state.get("defense_success")),
        "defense_success": defense_success,
        "defense_selected": bool((defender_execution.get("payload") or {}).get("action")),
        "defense_executed": defense_executed,
        "defense_confirmed": defense_confirmed,
        "defense_effects_confirmed": defense_effects_confirmed,
        "defense_applied_but_not_effective": defense_applied_but_not_effective,
        "security_progression_reduced": progression_reduced,
        "path_regressed": path_regressed,
        "attack_deactivated": attack_deactivated,
    }


def _canonical_stage_summary_text(
    *,
    stage_id: int,
    scenario_id: str,
    attacker_strategy_id: str,
    defender_strategy_id: str,
    defender_execution: dict[str, Any],
    validation: dict[str, Any],
    security_outcome: dict[str, Any],
    reasoning_summary: str,
) -> str:
    payload = defender_execution.get("payload") or {}
    action = str(payload.get("action") or "observe")
    target = str(payload.get("target") or "")
    path_stage = validation.get("path_stage_after", "unknown")
    comparable = bool(validation.get("comparable_stage"))
    defense_executed = bool(validation.get("defense_executed"))
    attack_active = bool(security_outcome.get("attack_active"))
    attack_effect_success = bool(security_outcome.get("attack_effect_success"))
    if not comparable:
        outcome = "no comparable attack stage"
    elif security_outcome["defense_confirmed"] and validation.get("path_regressed"):
        outcome = "attack pressure reduced"
    elif defense_executed and not security_outcome["defense_confirmed"]:
        outcome = "defense executed but not confirmed"
    elif attack_effect_success and not security_outcome["defense_success"]:
        outcome = "attack remained effective"
    elif not defense_executed and attack_active:
        outcome = "attack active; no defense executed"
    elif security_outcome["defense_success"]:
        outcome = "security progression was reduced"
    elif security_outcome["defense_applied_but_not_effective"]:
        outcome = "defense was applied and confirmed, but the attack remained effective"
    else:
        outcome = "attack remained effective after the defender stage"
    target_phrase = f" targeting {target}" if target else ""
    rationale = f" Rationale: {reasoning_summary}" if reasoning_summary else ""
    return (
        f"Stage {stage_id} for {scenario_id}: attacker {attacker_strategy_id} met defender "
        f"{defender_strategy_id}. Action {action}{target_phrase} ended with status "
        f"{defender_execution.get('status', 'unknown')}; defense_confirmed="
        f"{security_outcome['defense_confirmed']}; defense_effects_confirmed="
        f"{security_outcome['defense_effects_confirmed']}; defense_success="
        f"{security_outcome['defense_success']}. Path stage={path_stage}. Outcome: {outcome}.{rationale}"
    )


def build_stage_outcome(
    *,
    state: dict[str, Any],
    next_state: dict[str, Any] | None,
    attacker_execution: dict[str, Any],
    defender_execution: dict[str, Any],
    defender_result: Any,
) -> dict[str, Any]:
    current_state = dict(next_state or state)
    defense_status = defender_execution.get("status", "")
    live_environment_has_active_defenses = bool(
        current_state.get("defense_active")
        or current_state.get("drop_rules_active")
        or current_state.get("counters_stopped")
        or _safe_int((current_state.get("overhead") or {}).get("controller_active_actions")) > 0
    )

    if defense_status in ("dry_run", "observe_only") and not defender_execution.get("post_result"):
        current_state["drop_rules_active"] = False
        current_state["counters_stopped"] = False
        current_state["rate_limit_active"] = False
        current_state["defense_active"] = False
        current_state["defense_success"] = False
        overhead = dict(current_state.get("overhead") or {})
        overhead["controller_active_actions"] = 0
        overhead["flow_rules_installed"] = 0
        overhead["meters_added"] = 0
        current_state["overhead"] = overhead
        current_state["defense_evidence"] = {
            **(current_state.get("defense_evidence") or {}),
            "drop_rules_active": False,
            "rate_limit_active": False,
            "counters_stopped": False,
            "active_policy_actions": [],
            "flow_rules_installed": 0,
            "meters_added": 0,
            "source": "dry_run_no_execution",
        }
    comparable_stage = bool(state.get("attack_active")) or attacker_execution.get("status") == "dispatched"
    effect_validation = reconcile_defense_effects(
        {"defender": defender_result.selection},
        state,
        current_state,
        defender_execution,
    )
    if defense_status == "dry_run":
        effect_validation["defense_confirmed"] = False
        effect_validation["effects_confirmed"] = False
        effect_validation["observed_effects"] = []
        effect_validation["missing_effects"] = list(effect_validation.get("expected_effects") or [])
        effect_validation["semantic_observed_defense_effects"] = []
        effect_validation["semantic_missing_defense_effects"] = list(effect_validation.get("expected_effects") or [])
        effect_validation["reconciled_effects"] = {
            "drop_rules_active": False,
            "counters_stopped": False,
            "rate_limit_active": False,
        }
    if next_state is not None:
        current_state = merge_confirmed_defense_effects_into_state(current_state, effect_validation)
    else:
        current_state = merge_confirmed_defense_effects_into_state(current_state, effect_validation)
    security_outcome = resolve_security_outcome(
        previous_state=state,
        next_state=current_state,
        defender_execution=defender_execution,
        effect_validation=effect_validation,
    )
    if defense_status == "dry_run":
        security_outcome["defense_confirmed"] = False
        security_outcome["defense_effects_confirmed"] = False
        security_outcome["defense_success"] = False
        security_outcome["defense_applied_but_not_effective"] = False
    defense_confirmed = bool(security_outcome["defense_confirmed"])
    effects_confirmed = bool(security_outcome["defense_effects_confirmed"])
    attack_effect_success = bool(security_outcome["attack_effect_success"])
    defense_success = bool(security_outcome["defense_success"])
    execution_stage_success = bool(defense_status == "executed" and defense_confirmed)
    security_stage_success = bool(defense_success)
    llm_stage_success = bool(defender_result.request_success and defender_result.parse_success and not defender_result.fallback_used)
    defense_applied_but_not_effective = bool(security_outcome["defense_applied_but_not_effective"])
    containment_evidence = bool(
        security_outcome.get("path_regressed")
        or security_outcome.get("attack_deactivated")
        or (
            defense_confirmed
            and effects_confirmed
            and (
                effect_validation["reconciled_effects"].get("drop_rules_active")
                or effect_validation["reconciled_effects"].get("counters_stopped")
                or effect_validation["reconciled_effects"].get("rate_limit_active")
            )
        )
    )
    outcome_consistent = bool(
        attack_effect_success == bool(current_state.get("attack_effect_success"))
        and not (attack_effect_success and defense_success and not containment_evidence)
    )

    if not comparable_stage:
        stage_kind = "warmup"
    elif defense_status == "dry_run":
        stage_kind = "dry_run"
    elif defense_status == "observe_only":
        stage_kind = "observe_only"
    elif not defender_result.request_success:
        stage_kind = "llm_request_failed"
    elif defender_result.fallback_used:
        stage_kind = "llm_fallback"
    elif not defense_confirmed:
        stage_kind = "unconfirmed_execution"
    elif attack_effect_success and defense_success and containment_evidence:
        stage_kind = "defense_contained_after_attack_effect"
    elif defense_applied_but_not_effective:
        stage_kind = "defense_applied_but_not_effective"
    else:
        stage_kind = "experimental"

    invalidity_reasons: list[str] = []
    if not comparable_stage:
        invalidity_reasons.append("not_comparable")
    if not defender_result.request_success:
        invalidity_reasons.append("llm_request_failed")
    if not defender_result.parse_success:
        invalidity_reasons.append("llm_parse_failed")
    if defender_result.fallback_used:
        invalidity_reasons.append("llm_fallback_used")
    if defense_status == "dry_run":
        invalidity_reasons.append("defender_dry_run")
    if not outcome_consistent:
        invalidity_reasons.append("inconsistent_outcome")

    paper_valid_stage = bool(
        comparable_stage
        and defender_result.request_success
        and defender_result.parse_success
        and not defender_result.fallback_used
        and defense_status != "dry_run"
        and outcome_consistent
    )
    learning_valid_stage = bool(paper_valid_stage and execution_stage_success and security_stage_success)
    stage_success = bool(security_stage_success)
    operationally_executed_debug_stage = bool(
        defender_execution.get("status") == "executed" and not paper_valid_stage
    )

    state_summary = {
        "scenario_id": current_state.get("scenario_id", state.get("scenario_id")),
        "attack_active": bool(current_state.get("attack_active")),
        "attack_success": bool(current_state.get("attack_success", current_state.get("attack_effect_success"))),
        "attack_effect_success": attack_effect_success,
        "defense_success": defense_success,
        "raw_state_defense_success": bool(security_outcome["raw_state_defense_success"]),
        "gateway_seen": bool(current_state.get("gateway_seen") or ((current_state.get("defender_observation") or {}).get("gateway_seen"))),
        "worker_seen": bool(current_state.get("worker_seen") or ((current_state.get("defender_observation") or {}).get("worker_seen"))),
        "cloud_seen": bool(current_state.get("cloud_seen") or ((current_state.get("defender_observation") or {}).get("cloud_seen"))),
        "path_stage": _safe_int(current_state.get("path_stage")),
        "path_stage_label": current_state.get("path_stage_label", ""),
        "drop_rules_active": bool(effect_validation["reconciled_effects"].get("drop_rules_active")),
        "counters_stopped": bool(effect_validation["reconciled_effects"].get("counters_stopped")),
        "rate_limit_active": bool(effect_validation["reconciled_effects"].get("rate_limit_active")),
        "raw_drop_rules_active": bool((current_state.get("raw_state_effects") or {}).get("drop_rules_active")),
        "raw_counters_stopped": bool((current_state.get("raw_state_effects") or {}).get("counters_stopped")),
        "raw_rate_limit_active": bool((current_state.get("raw_state_effects") or {}).get("rate_limit_active")),
        "normalized_drop_rules_active": bool((current_state.get("normalized_state_effects") or {}).get("drop_rules_active")),
        "normalized_counters_stopped": bool((current_state.get("normalized_state_effects") or {}).get("counters_stopped")),
        "normalized_rate_limit_active": bool((current_state.get("normalized_state_effects") or {}).get("rate_limit_active")),
        "controller_active_actions": effect_validation["active_policy_actions"],
        "flow_rules_installed": effect_validation["flow_rules_installed"],
        "meters_added": effect_validation["meters_added"],
        "live_environment_has_active_defenses": live_environment_has_active_defenses,
        "eval_action_applied": defense_status == "executed",
        "eval_action_effect_observed": effects_confirmed if defense_status == "executed" else False,
    }

    validation = {
        "stage_kind": stage_kind,
        "comparable_stage": comparable_stage,
        "llm_request_success": bool(defender_result.request_success),
        "llm_request_error": getattr(defender_result, "request_error", ""),
        "llm_parse_success": bool(defender_result.parse_success),
        "llm_recovery_used": bool(getattr(defender_result, "recovery_used", False)),
        "llm_stage_valid": paper_valid_stage,
        "paper_valid_stage": paper_valid_stage,
        "learning_valid_stage": learning_valid_stage,
        "operationally_executed_debug_stage": operationally_executed_debug_stage,
        "invalidity_reasons": invalidity_reasons,
        "defender_execution_status": defense_status,
        "defense_confirmed": defense_confirmed,
        "defense_effects_confirmed": effects_confirmed,
        "defense_effect_observed": effects_confirmed if defense_status == "executed" else False,
        "semantic_containment": bool(
            defense_status == "executed"
            and (effect_validation["reconciled_effects"].get("drop_rules_active") or effect_validation["reconciled_effects"].get("counters_stopped"))
        ),
        "attribution_source": "dry_run_no_execution" if defense_status == "dry_run" else "live_execution",
        "expected_defense_effects": effect_validation["expected_effects"],
        "observed_defense_effects": effect_validation["observed_effects"],
        "missing_defense_effects": effect_validation["missing_effects"],
        "semantic_observed_defense_effects": effect_validation["semantic_observed_defense_effects"],
        "semantic_missing_defense_effects": effect_validation["semantic_missing_defense_effects"],
        "semantic_effect_mapping_details": effect_validation["semantic_effect_mapping_details"],
        "defense_effect_mismatch": bool(defense_confirmed and not effects_confirmed),
        "effect_source_conflict": bool(effect_validation["effect_source_conflict"]),
        "effect_source_details": effect_validation["effect_source_details"],
        "raw_state_effects": current_state.get("raw_state_effects", {}),
        "normalized_state_effects": current_state.get("normalized_state_effects", {}),
        "state_effect_sources": current_state.get("state_effect_sources", {}),
        "effect_resolution_policy": current_state.get("effect_resolution_policy", ""),
        "defense_selected": bool(security_outcome["defense_selected"]),
        "defense_executed": bool(security_outcome["defense_executed"]),
        "defense_applied": defender_execution.get("status") == "executed",
        "defense_applied_but_not_effective": defense_applied_but_not_effective,
        "path_stage_before": _safe_int(state.get("path_stage")),
        "path_stage_after": _safe_int(current_state.get("path_stage")),
        "security_progression_reduced": bool(security_outcome["security_progression_reduced"]),
        "path_regressed": bool(security_outcome["path_regressed"]),
        "attack_deactivated": bool(security_outcome["attack_deactivated"]),
        "containment_evidence": containment_evidence,
        "llm_stage_success": llm_stage_success,
        "execution_stage_success": execution_stage_success,
        "security_stage_success": security_stage_success,
        "stage_success": stage_success,
    }
    return {
        "state_summary": state_summary,
        "stage_validation": validation,
        "security_outcome": security_outcome,
    }


def _execute_llm_defender(
    *,
    modules: Any,
    selection_pair: dict[str, Any],
    state: dict[str, Any],
    attacker_execution: dict[str, Any],
    defender_result: Any,
    execute: bool,
    ryu_action_url: str,
    cloud_policy_url: str,
    timeout: float,
) -> dict[str, Any]:
    payload = modules.strategy_runtime.action_payload_from_defender(selection_pair.get("defender"))
    if payload is None:
        return {"status": "no_active_defender_strategy"}

    payload = dict(payload)
    payload["source"] = "llm_defender"
    payload["decision_source"] = "llm_defender"
    payload["llm_reasoning_summary"] = (selection_pair.get("defender") or {}).get("reasoning_summary", "")

    policy_context_result = None
    policy_context_payload = None
    if cloud_policy_url:
        policy_context_payload = _build_llm_cloud_policy_context(
            modules,
            state,
            selection_pair,
            attacker_execution,
            defender_result,
        )
        context_url = modules.strategy_runtime.endpoint_url(cloud_policy_url, "/context")
        policy_context_result = modules.strategy_runtime.post_json_with_container_fallback(
            context_url,
            policy_context_payload,
            timeout=timeout,
            docker_container=modules.strategy_runtime.DEFAULT_CLOUD_POLICY_CONTAINER,
            docker_fallback=modules.strategy_runtime.DEFAULT_CLOUD_POLICY_DOCKER_FALLBACK,
            fallback_path="/context",
        )
        context_body = _parse_json_body(policy_context_result)
        merged_context_body = {
            **context_body,
            "decision_source": "llm_defender",
            "policy_mode": "llm_direct_ryu",
            "observe_only": payload.get("action") == "observe",
            "cloud_policy_observe_only_normalized": payload.get("action") == "observe",
            "cloud_policy_observe_only_raw": context_body.get("observe_only"),
            "selected_action": {
                "type": payload.get("action", ""),
                "target": payload.get("target", ""),
                "reason": "LLM-selected defender strategy was sent directly to Ryu.",
                "ryu_intent": payload,
            },
        }
        policy_context_result = {
            **policy_context_result,
            "raw_body": policy_context_result.get("body", ""),
            "body": json.dumps(merged_context_body, sort_keys=True),
            "json": merged_context_body,
        }

    policy_decision_result = {
        "status": "skipped",
        "reason": "llm_selected_direct_ryu_intent",
        "json": {
            "decision_source": "llm_defender",
            "policy_mode": "llm_direct_ryu",
            "selected_action": {
                "type": payload.get("action", ""),
                "target": payload.get("target", ""),
                "reason": "LLM-selected defender strategy was sent directly to Ryu.",
                "ryu_intent": payload,
            },
            "observe_only": payload.get("action") == "observe",
            "cloud_policy_observe_only_normalized": payload.get("action") == "observe",
        },
    }

    if payload.get("action") == "observe":
        return {
            "status": "observe_only",
            "payload": payload,
            "cloud_policy_context": policy_context_result,
            "cloud_policy_context_payload": policy_context_payload,
            "cloud_policy_decision": policy_decision_result,
        }

    if not execute:
        return {
            "status": "dry_run",
            "payload": payload,
            "cloud_policy_context": policy_context_result,
            "cloud_policy_context_payload": policy_context_payload,
            "cloud_policy_decision": policy_decision_result,
        }

    result = modules.strategy_runtime.post_json(ryu_action_url, payload, timeout=timeout)
    return {
        "status": "executed" if result.get("ok") else "execution_failed",
        "payload": payload,
        "cloud_policy_context": policy_context_result,
        "cloud_policy_context_payload": policy_context_payload,
        "cloud_policy_decision": policy_decision_result,
        "post_result": result,
    }


def _stage_validation(
    *,
    state: dict[str, Any],
    next_state: dict[str, Any] | None,
    attacker_execution: dict[str, Any],
    defender_execution: dict[str, Any],
    defender_result: Any,
    modules: Any,
) -> dict[str, Any]:
    _ = modules
    return build_stage_outcome(
        state=state,
        next_state=next_state,
        attacker_execution=attacker_execution,
        defender_execution=defender_execution,
        defender_result=defender_result,
    )["stage_validation"]


def _baseline_top_defender(active_defenders: list[dict[str, Any]], game: dict[str, Any]) -> dict[str, Any]:
    return select_baseline_top_defender(active_defenders, game)


def run_stage(
    *,
    model_config_path: str | Path,
    scenario_id: str,
    strategy_space: str | Path | None = None,
    scenario_registry: str | Path | None = None,
    mulval_policy: str | Path | None = None,
    core_url: str | None = None,
    mtd_metrics_url: str | None = None,
    mtd_status_url: str | None = None,
    cloud_policy_url: str | None = None,
    cloud_logger_url: str | None = None,
    ryu_action_url: str | None = None,
    attacker_dispatch_url: str | None = None,
    execute_attacker: bool | None = None,
    execute_defender: bool | None = None,
    observe_delay_seconds: float | None = None,
    selection_mode: str | None = None,
    random_seed: int | None = None,
    population_file: str | Path | None = None,
    stage_log: str | Path | None = None,
    decision_trace_log: str | Path | None = None,
    summary_log: str | Path | None = None,
    output_root: str | Path | None = None,
    timeout_seconds: float | None = None,
    llm_timeout_seconds: float | None = None,
    llm_max_retries: int | None = None,
    llm_compact_prompt: bool = False,
    llm_max_candidate_fields: int | None = None,
    strict_preconditions: bool = False,
    max_attack_cost: float = 1.0,
    max_defense_cost: float = 1.0,
    no_disruptive_defense: bool = False,
    offline: bool = False,
    no_observe_next_state: bool = False,
    no_population_load: bool = False,
    no_save_population: bool = False,
    no_stage_log: bool = False,
    no_decision_trace_log: bool = False,
    no_auto_defense_event: bool = False,
    strict_comparable_strategies: bool = False,
) -> dict[str, Any]:
    config = ResolvedConfig.from_model_config(model_config_path, output_root=output_root)
    modules = load_emo_strategy_modules(config.project_root)

    game_dir = strategy_dir(config.project_root)
    default_population_file = config.raw_output_dir / "live_population_state.json"
    default_stage_log = config.raw_output_dir / "live_stage_history.jsonl"
    default_decision_log = config.raw_output_dir / "live_decision_trace.jsonl"
    default_summary_log = config.raw_output_dir / "stage_summaries.jsonl"

    trial_cfg = config.trial_config()
    emulator_cfg = config.emulator_config()

    strategy_space_path = _resolve_path(
        config.project_root,
        strategy_space,
        game_dir / "strategy_space.json",
    )
    scenario_registry_path = _resolve_path(
        config.project_root,
        scenario_registry or config.data_paths()["scenario_registry"],
        config.project_root / "attacker" / "scenarios" / "attack_scenarios.json",
    )
    mulval_policy_path = _resolve_path(
        config.project_root,
        mulval_policy or config.data_paths()["mulval_policy"],
        config.project_root / "attacker" / "mulval" / "outputs" / "base_edge2_policy.json",
    )
    population_file_path = _resolve_path(config.project_root, population_file, default_population_file)
    stage_log_path = _resolve_path(config.project_root, stage_log, default_stage_log)
    decision_log_path = _resolve_path(config.project_root, decision_trace_log, default_decision_log)
    summary_log_path = _resolve_path(config.project_root, summary_log, default_summary_log)

    effective_observe_delay = float(_choice(observe_delay_seconds, trial_cfg.get("observe_delay_seconds", 45.0)))
    effective_execute_defender = bool(_choice(execute_defender, trial_cfg.get("execute_defender", True)))
    effective_execute_attacker = bool(_choice(execute_attacker, trial_cfg.get("execute_attacker", True)))
    effective_timeout = float(_choice(timeout_seconds, emulator_cfg.get("timeout_seconds", 3.0)))
    effective_llm_timeout_seconds = float(
        _choice(
            llm_timeout_seconds,
            config.llm_config().get(
                "llm_timeout_seconds",
                config.llm_config().get("timeout_seconds", 120.0),
            ),
        )
    )
    effective_core_url = core_url or emulator_cfg.get("core_url")
    effective_mtd_metrics_url = mtd_metrics_url or emulator_cfg.get("ryu_metrics_url")
    effective_mtd_status_url = mtd_status_url or emulator_cfg.get("ryu_status_url")
    effective_cloud_policy_url = cloud_policy_url or emulator_cfg.get("cloud_policy_url", "")
    effective_cloud_logger_url = cloud_logger_url or emulator_cfg.get("cloud_logger_url", "")
    effective_ryu_action_url = ryu_action_url or emulator_cfg.get("ryu_action_url")
    effective_attacker_dispatch_url = attacker_dispatch_url or modules.strategy_runtime.DEFAULT_ATTACKER_DISPATCH_URL
    runtime_stage_log_path = config.project_root / "game" / "stage_history.jsonl"

    constraints = {
        "strict_preconditions": strict_preconditions,
        "max_attack_cost": max_attack_cost,
        "max_defense_cost": max_defense_cost,
        "allow_disruptive_defense": not no_disruptive_defense,
    }
    pre_stage_reset = None
    if effective_execute_defender:
        pre_stage_reset = modules.strategy_runtime.stage_teardown(
            effective_ryu_action_url,
            effective_mtd_status_url,
            effective_timeout,
            reset_actions=True,
        )

    stage_state = modules.state_builder.build_state(
        core_url=None if offline else effective_core_url,
        mtd_metrics_url=None if offline else effective_mtd_metrics_url,
        mtd_status_url=None if offline else effective_mtd_status_url,
        scenario_id=scenario_id or None,
        scenario_registry_path=scenario_registry_path,
        mulval_policy_path=mulval_policy_path,
        timeout=effective_timeout,
        constraints=constraints,
    )
    cloud_storage_baseline = _safe_float(
        ((stage_state.get("workload") or {}).get("cloud_storage_confirmations")),
        0.0,
    )

    manager = modules.strategy_manager.StrategyManager.from_file(strategy_space_path)
    strategy_space_checksum = hashlib.sha256(
        strategy_space_path.read_bytes()
    ).hexdigest()[:16] if strategy_space_path.exists() else ""
    effective_selection_mode = selection_mode or str(
        manager.parameters.get("selection_mode", "dominant")
    )
    active = manager.active_lists(stage_state, strict_preconditions=strict_preconditions)
    runtime_reference = _read_latest_jsonl_row(runtime_stage_log_path)
    runtime_checksum = str(runtime_reference.get("strategy_space_checksum") or "").strip()
    runtime_active_attackers = runtime_reference.get("active_attacker_ids") or []
    runtime_active_defenders = runtime_reference.get("active_defender_ids") or []
    strategy_mismatch = bool(
        (runtime_checksum and runtime_checksum != strategy_space_checksum)
        or (runtime_active_attackers and runtime_active_attackers != active["attacker_ids"])
        or (runtime_active_defenders and runtime_active_defenders != active["defender_ids"])
    )
    if strategy_mismatch and strict_comparable_strategies:
        raise RuntimeError(
            "Runtime and eval strategy spaces differ under strict comparable mode. "
            f"runtime_checksum={runtime_checksum or 'missing'} eval_checksum={strategy_space_checksum or 'missing'}"
        )
    previous_population = modules.strategy_runtime.load_population(
        None if no_population_load else population_file_path
    )
    game = modules.game_model.evolutionary_step(
        active["attackers"],
        active["defenders"],
        stage_state,
        previous_population=previous_population,
        parameters=manager.parameters,
    )
    modules.strategy_runtime.attach_global_populations(
        game,
        previous_population,
        manager,
        str(stage_state.get("scenario_id") or scenario_id),
    )

    attacker_selection = modules.policy_selector.select_strategy(
        active["attackers"],
        game.get("attacker_population", {}),
        game.get("attacker_utilities", {}),
        mode=effective_selection_mode,
        random_seed=random_seed,
    )

    attacker_execution = modules.strategy_runtime.execute_attacker(
        attacker_selection,
        stage_state,
        execute=effective_execute_attacker,
        dispatch_url=effective_attacker_dispatch_url,
        cloud_logger_url=effective_cloud_logger_url,
        cloud_policy_url=effective_cloud_policy_url,
        timeout=effective_timeout,
    )

    previous_stage_memory = _read_latest_stage_memory(
        stage_log_path,
        str(stage_state.get("scenario_id", scenario_id)),
        active_defender_ids={str(d.get("id") or "") for d in active["defenders"]},
    )
    baseline_top_defender = _baseline_top_defender(active["defenders"], game)
    defender_result = select_defender_strategy(
        llm_config=config.llm_config(),
        live_state=stage_state,
        selected_attacker=modules.policy_selector.compact_selection(attacker_selection) or {},
        attacker_execution=attacker_execution,
        active_defenders=active["defenders"],
        game_result=game,
        stage_memory=previous_stage_memory,
        llm_timeout_seconds=effective_llm_timeout_seconds,
        llm_max_retries=llm_max_retries,
        llm_compact_prompt=llm_compact_prompt,
        llm_max_candidate_fields=llm_max_candidate_fields,
    )
    defender_selection = defender_result.selection

    selection_pair = {
        "attacker": attacker_selection,
        "defender": defender_selection,
    }

    defender_execution = _execute_llm_defender(
        modules=modules,
        selection_pair=selection_pair,
        state=stage_state,
        attacker_execution=attacker_execution,
        defender_result=defender_result,
        execute=effective_execute_defender,
        ryu_action_url=effective_ryu_action_url,
        cloud_policy_url=effective_cloud_policy_url,
        timeout=effective_timeout,
    )
    execution = {
        "attacker": attacker_execution,
        "defender": defender_execution,
    }
    if defender_execution.get("status") == "executed" and not offline:
        execution["defender"]["commit_confirmation"] = (
            modules.strategy_runtime.wait_for_defense_commit(
                execution["defender"], effective_mtd_status_url, effective_timeout
            )
        )

    next_state = None
    if not no_observe_next_state:
        if effective_observe_delay > 0:
            time.sleep(effective_observe_delay)
        next_constraints = {
            **constraints,
            "cloud_storage_baseline": cloud_storage_baseline,
            "attacker_execution": attacker_execution,
        }
        next_state = modules.state_builder.build_state(
            core_url=None if offline else effective_core_url,
            mtd_metrics_url=None if offline else effective_mtd_metrics_url,
            mtd_status_url=None if offline else effective_mtd_status_url,
            scenario_id=scenario_id or stage_state.get("scenario_id"),
            scenario_registry_path=scenario_registry_path,
            mulval_policy_path=mulval_policy_path,
            timeout=effective_timeout,
            constraints=next_constraints,
        )

    if not offline and not no_auto_defense_event:
        execution["defender"]["defense_event"] = modules.strategy_runtime.post_defense_result_event(
            effective_cloud_logger_url,
            stage_state,
            next_state,
            selection_pair,
            execution,
            effective_timeout,
        )

    outcome = build_stage_outcome(
        state=stage_state,
        next_state=next_state,
        attacker_execution=attacker_execution,
        defender_execution=execution["defender"],
        defender_result=defender_result,
    )
    validation = outcome["stage_validation"]
    previous_warmups = consecutive_stage_kind_count(
        summary_log_path,
        str(stage_state.get("scenario_id") or scenario_id),
        "warmup",
    )
    consecutive_warmup_count = (
        previous_warmups + 1 if validation.get("stage_kind") == "warmup" else 0
    )
    max_warmups = int(trial_cfg.get("max_consecutive_warmup", 3))
    attacker_body = _parse_json_body(attacker_execution.get("post_result") or {})
    selected_agents = attacker_body.get("selected_agents") or []
    validation.update({
        "consecutive_warmup_count": consecutive_warmup_count,
        "max_consecutive_warmup": max_warmups,
        "run_unhealthy": consecutive_warmup_count >= max_warmups,
        "agent_alive_count": len(selected_agents) if isinstance(selected_agents, list) else 0,
        "attack_redispatch_attempted": bool(attacker_execution.get("dispatch_attempted")),
        "attack_redispach_attempted": bool(attacker_execution.get("dispatch_attempted")),
        "attack_restart_reason": (
            "consecutive_warmup_no_comparable_attack"
            if consecutive_warmup_count >= max_warmups
            else ""
        ),
    })
    prior_total, prior_valid = stage_validity_counts(
        summary_log_path, str(stage_state.get("scenario_id") or scenario_id)
    )
    validity_rate = (prior_valid + int(bool(validation.get("paper_valid_stage")))) / max(prior_total + 1, 1)
    minimum_validity_rate = float(trial_cfg.get("minimum_paper_valid_rate", 0.5))
    validation.update({
        "paper_valid_rate_so_far": validity_rate,
        "minimum_paper_valid_rate": minimum_validity_rate,
        "low_paper_valid_rate_warning": validity_rate < minimum_validity_rate,
    })
    if validation["run_unhealthy"]:
        reasons = list(validation.get("invalidity_reasons") or [])
        if "consecutive_warmup_threshold" not in reasons:
            reasons.append("consecutive_warmup_threshold")
        validation["invalidity_reasons"] = reasons

    compact_pair = {
        "attacker": modules.policy_selector.compact_selection(attacker_selection),
        "defender": modules.policy_selector.compact_selection(defender_selection),
    }
    transition = modules.stage_transition.build_transition_record(
        stage_state,
        next_state,
        compact_pair,
        execution,
        game,
    )
    decision_trace = modules.stage_transition.build_decision_trace_record(
        stage_state,
        next_state,
        compact_pair,
        execution,
        game,
        transition_id=transition.get("transition_id", ""),
    )
    transition["state_summary"] = {
        **(transition.get("state_summary") or {}),
        **outcome["state_summary"],
    }
    decision_trace["state_summary"] = {
        **(decision_trace.get("state_summary") or {}),
        **outcome["state_summary"],
    }

    stage_id = _next_stage_id(stage_log_path)
    raw_llm_selected_defender_strategy_id = str(defender_result.raw_selected_strategy_id or "")
    final_defender_strategy_id = str(defender_selection.get("id", ""))
    final_defender_action = str(defender_selection.get("action") or "")
    final_defender_target = str(defender_selection.get("target") or "")
    fallback_used = bool(defender_result.fallback_used and raw_llm_selected_defender_strategy_id != final_defender_strategy_id)
    alignment = compute_llm_baseline_alignment(
        baseline_top_defender_strategy_id=str(baseline_top_defender.get("strategy_id") or defender_result.baseline_top_strategy_id or ""),
        raw_llm_selected_defender_strategy_id=raw_llm_selected_defender_strategy_id,
        final_defender_strategy_id=final_defender_strategy_id,
        executed_via_fallback=fallback_used,
    )
    executed_decision_source = (
        "constraint_fallback" if fallback_used else str(defender_result.executed_decision_source or "llm_defender")
    )
    executed_decision_reason = (
        str(defender_result.fallback_reason or "llm_fallback_used")
        if fallback_used
        else "llm_selected_strategy"
    )
    executed_decision_reasoning_summary = (
        str(defender_selection.get("reasoning_summary", ""))
        if fallback_used
        else str(defender_result.raw_reasoning_summary or defender_selection.get("reasoning_summary", ""))
    )
    fallback_resolution = {
        "fallback_used": bool(defender_result.fallback_used),
        "raw_llm_selected_defender_strategy_id": raw_llm_selected_defender_strategy_id,
        "final_defender_strategy_id": final_defender_strategy_id,
        "constraint_name": str(defender_result.fallback_constraint_name or ""),
        "constraint_trigger": {
            "attack_active": bool(stage_state.get("attack_active")),
            "path_stage": _safe_int(stage_state.get("path_stage")),
            "urgency_level": str(defender_result.urgency_level or ""),
        },
        "fallback_reason": str(defender_result.fallback_reason or ""),
        "fallback_selection_policy": "highest_priority_safe_containment" if fallback_used else "",
        "fallback_explanation": (
            executed_decision_reasoning_summary
            if fallback_used
            else ""
        ),
    }
    candidate_rankings = enrich_candidate_rankings(
        defender_result.ranked_candidates,
        active["defenders"],
        game,
    )
    llm_metadata = {
        "provider": defender_result.trace.provider,
        "model": defender_result.trace.model_name,
        "model_name": defender_result.trace.model_name,
        "llm_timeout_seconds": effective_llm_timeout_seconds,
        "llm_latency_ms": defender_result.trace.latency_ms,
        "llm_timeout": "timed out" in str(defender_result.request_error or "").lower(),
        "reasoning_summary": executed_decision_reasoning_summary,
        "raw_llm_reasoning_summary": defender_result.raw_reasoning_summary,
        "raw_llm_selected_defender_strategy_id": raw_llm_selected_defender_strategy_id,
        "raw_llm_baseline_alignment": alignment["raw_llm_alignment_vs_baseline"],
        "raw_llm_override_reason": defender_result.raw_override_reason,
        "raw_llm_ranked_candidates": defender_result.raw_ranked_candidates,
        "fallback_used": fallback_used,
        "fallback_reason": defender_result.fallback_reason,
        "fallback_constraint_name": str(defender_result.fallback_constraint_name or ""),
        "executed_via_fallback": fallback_used,
        "executed_decision_source": executed_decision_source,
        "executed_decision_reason": executed_decision_reason,
        "executed_decision_reasoning_summary": executed_decision_reasoning_summary,
        "request_success": defender_result.request_success,
        "request_error": defender_result.request_error,
        "parse_success": defender_result.parse_success,
        "recovery_used": defender_result.recovery_used,
        "confidence": defender_selection.get("confidence", 0.0),
        "expected_security_gain": defender_selection.get("expected_security_gain", 0.0),
        "expected_qos_impact": defender_selection.get("expected_qos_impact", 0.0),
        "expected_controller_cost": defender_selection.get("expected_controller_cost", 0.0),
        "baseline_top_defender_strategy_id": baseline_top_defender.get("strategy_id", defender_result.baseline_top_strategy_id),
        "baseline_top_defender_utility": baseline_top_defender.get("utility", defender_result.baseline_top_utility),
        "baseline_top_defender_population_share": baseline_top_defender.get("population_share", 0.0),
        "baseline_top_defender_tiebreak_reason": baseline_top_defender.get("tiebreak_reason", ""),
        "llm_selected_defender_strategy_id": raw_llm_selected_defender_strategy_id,
        "executed_defender_strategy_id": final_defender_strategy_id,
        "final_defender_strategy_id": final_defender_strategy_id,
        "final_defender_action": final_defender_action,
        "final_defender_target": final_defender_target,
        "llm_ranked_candidates": candidate_rankings,
        **alignment,
        "llm_override_reason": defender_result.override_reason,
        "llm_urgency_level": defender_result.urgency_level,
        "llm_decision_mode": defender_result.decision_mode,
        "llm_telemetry_confidence": defender_result.telemetry_confidence,
        "llm_repeat_previous_action": defender_result.repeat_previous_action,
        "llm_why_not_observe": defender_result.why_not_observe,
        "llm_why_not_rate_limit": defender_result.why_not_rate_limit,
        "llm_why_not_quarantine": defender_result.why_not_quarantine,
        "llm_stage_memory_used": defender_result.stage_memory_used,
        "stage_memory": previous_stage_memory,
        "baseline_top_defender": baseline_top_defender,
        "fallback_resolution": fallback_resolution,
    }
    stage_summary = build_stage_summary_record(
        stage_id=stage_id,
        scenario_id=str(stage_state.get("scenario_id", scenario_id)),
        attacker_strategy_id=str((compact_pair.get("attacker") or {}).get("id", "")),
        defender_strategy_id=str((compact_pair.get("defender") or {}).get("id", "")),
        reasoning_summary=executed_decision_reasoning_summary,
        previous_state=stage_state,
        next_state=next_state,
        execution={
            "attacker_status": attacker_execution.get("status", ""),
            "defender_status": defender_execution.get("status", ""),
            "defender_action": (defender_execution.get("payload") or {}).get("action", ""),
            "defender_target": (defender_execution.get("payload") or {}).get("target", ""),
            "defense_confirmed": validation["defense_confirmed"],
            "defense_effects_confirmed": validation["defense_effects_confirmed"],
            "stage_valid": validation["llm_stage_valid"],
            "stage_success": validation["stage_success"],
            "stage_kind": validation["stage_kind"],
            "defense_applied_but_not_effective": validation["defense_applied_but_not_effective"],
            "executed_via_fallback": fallback_used,
        },
        llm_config=config.llm_config(),
        summary_template_path=config.prompt_paths().get("summary_template"),
    )
    stage_summary_record = {
        **stage_summary.record,
        "recorded_at": _utc_now_iso(),
        "summary_text": _canonical_stage_summary_text(
            stage_id=stage_id,
            scenario_id=str(stage_state.get("scenario_id", scenario_id)),
            attacker_strategy_id=str((compact_pair.get("attacker") or {}).get("id", "")),
            defender_strategy_id=str((compact_pair.get("defender") or {}).get("id", "")),
            defender_execution=execution["defender"],
            validation=validation,
            security_outcome=outcome["security_outcome"],
            reasoning_summary=executed_decision_reasoning_summary,
        ),
        "security_outcome": outcome["security_outcome"],
        "execution": {
            **(stage_summary.record.get("execution") or {}),
            "defense_selected": validation["defense_selected"],
            "defense_executed": validation["defense_executed"],
            "defense_confirmed": validation["defense_confirmed"],
            "defense_effects_confirmed": validation["defense_effects_confirmed"],
            "defense_applied_but_not_effective": validation["defense_applied_but_not_effective"],
            "stage_valid": validation["paper_valid_stage"],
            "stage_success": validation["stage_success"],
            "stage_kind": validation["stage_kind"],
            "executed_via_fallback": fallback_used,
            "operationally_executed_debug_stage": validation["operationally_executed_debug_stage"],
        },
        "stage_validation": validation,
        "qos_snapshot_before_collected": bool(stage_state.get("qos")),
        "qos_snapshot_after_collected": bool(next_state and next_state.get("qos")),
        "qos_collection_error": "; ".join((next_state or {}).get("source_errors", [])),
        "all_qos_deltas_zero_possible_probe_failure": bool(
            next_state
            and all(
                abs(_safe_float((stage_summary.record.get("qos_delta") or {}).get(key))) < 1e-12
                for key in (
                    "sensor_to_edge_latency_ms",
                    "edge_to_cloud_latency_ms",
                    "throughput_bytes_per_second",
                )
            )
        ),
        "llm": {
            "provider": llm_metadata["provider"],
            "model": llm_metadata["model"],
            "model_name": llm_metadata["model_name"],
            "latency_ms": llm_metadata["llm_latency_ms"],
            "request_success": llm_metadata["request_success"],
            "parse_success": llm_metadata["parse_success"],
            "timeout": llm_metadata["llm_timeout"],
            "timeout_seconds": llm_metadata["llm_timeout_seconds"],
            "latency_warning": llm_metadata["llm_latency_ms"] > 30_000,
            "reasoning_summary": llm_metadata["reasoning_summary"],
            "raw_llm_reasoning_summary": llm_metadata["raw_llm_reasoning_summary"],
            "fallback_used": llm_metadata["fallback_used"],
            "baseline_alignment": llm_metadata["llm_baseline_alignment"],
            "raw_llm_baseline_alignment": llm_metadata["raw_llm_baseline_alignment"],
            "raw_llm_alignment_vs_baseline": llm_metadata["raw_llm_alignment_vs_baseline"],
            "final_decision_alignment_vs_baseline": llm_metadata["final_decision_alignment_vs_baseline"],
            "override_reason": llm_metadata["llm_override_reason"],
            "urgency_level": llm_metadata["llm_urgency_level"],
            "decision_mode": llm_metadata["llm_decision_mode"],
            "telemetry_confidence": llm_metadata["llm_telemetry_confidence"],
            "repeat_previous_action": llm_metadata["llm_repeat_previous_action"],
            "why_not_observe": llm_metadata["llm_why_not_observe"],
            "why_not_rate_limit": llm_metadata["llm_why_not_rate_limit"],
            "why_not_quarantine": llm_metadata["llm_why_not_quarantine"],
            "raw_llm_selected_defender_strategy_id": llm_metadata["raw_llm_selected_defender_strategy_id"],
            "final_defender_strategy_id": llm_metadata["final_defender_strategy_id"],
            "executed_via_fallback": llm_metadata["executed_via_fallback"],
            "fallback_reason": llm_metadata["fallback_reason"],
            "executed_decision_reasoning_summary": llm_metadata["executed_decision_reasoning_summary"],
            "baseline_top_defender_strategy_id": llm_metadata["baseline_top_defender_strategy_id"],
            "baseline_top_defender_utility": llm_metadata["baseline_top_defender_utility"],
            "baseline_top_defender_population_share": llm_metadata["baseline_top_defender_population_share"],
            "baseline_top_defender_tiebreak_reason": llm_metadata["baseline_top_defender_tiebreak_reason"],
            "candidate_rankings": llm_metadata["llm_ranked_candidates"],
        },
        "fallback_resolution": fallback_resolution,
    }

    transition.update(
        {
            "stage_id": stage_id,
            "decision_source": "llm_defender",
            "strategy_space_checksum": strategy_space_checksum,
            "strategy_mismatch": strategy_mismatch,
            "active_attacker_ids": active["attacker_ids"],
            "active_defender_ids": active["defender_ids"],
            "filtered_out_reasons": active.get("filtered_out_reasons", {}),
            "pre_stage_reset": pre_stage_reset,
            "llm": llm_metadata,
            "stage_validation": validation,
            "stage_summary": stage_summary_record,
            "baseline_top_defender_strategy_id": llm_metadata["baseline_top_defender_strategy_id"],
            "baseline_top_defender_utility": llm_metadata["baseline_top_defender_utility"],
            "baseline_top_defender_population_share": llm_metadata["baseline_top_defender_population_share"],
            "baseline_top_defender_tiebreak_reason": llm_metadata["baseline_top_defender_tiebreak_reason"],
            "llm_selected_defender_strategy_id": llm_metadata["llm_selected_defender_strategy_id"],
            "raw_llm_selected_defender_strategy_id": llm_metadata["raw_llm_selected_defender_strategy_id"],
            "final_defender_strategy_id": llm_metadata["final_defender_strategy_id"],
            "final_defender_action": llm_metadata["final_defender_action"],
            "final_defender_target": llm_metadata["final_defender_target"],
            "executed_via_fallback": llm_metadata["executed_via_fallback"],
            "fallback_reason": llm_metadata["fallback_reason"],
            "raw_llm_reasoning_summary": llm_metadata["raw_llm_reasoning_summary"],
            "executed_decision_reasoning_summary": llm_metadata["executed_decision_reasoning_summary"],
            "llm_ranked_candidates": llm_metadata["llm_ranked_candidates"],
            "llm_baseline_alignment": llm_metadata["llm_baseline_alignment"],
            "raw_llm_alignment_vs_baseline": llm_metadata["raw_llm_alignment_vs_baseline"],
            "final_decision_alignment_vs_baseline": llm_metadata["final_decision_alignment_vs_baseline"],
            "final_baseline_alignment": llm_metadata["final_baseline_alignment"],
            "llm_override_reason": llm_metadata["llm_override_reason"],
            "llm_urgency_level": llm_metadata["llm_urgency_level"],
            "llm_decision_mode": llm_metadata["llm_decision_mode"],
            "llm_telemetry_confidence": llm_metadata["llm_telemetry_confidence"],
            "llm_repeat_previous_action": llm_metadata["llm_repeat_previous_action"],
            "llm_why_not_observe": llm_metadata["llm_why_not_observe"],
            "llm_why_not_rate_limit": llm_metadata["llm_why_not_rate_limit"],
            "llm_why_not_quarantine": llm_metadata["llm_why_not_quarantine"],
            "llm_expected_security_gain": llm_metadata["expected_security_gain"],
            "llm_expected_qos_impact": llm_metadata["expected_qos_impact"],
            "llm_expected_controller_cost": llm_metadata["expected_controller_cost"],
            "llm_stage_memory_used": llm_metadata["llm_stage_memory_used"],
            "fallback_resolution": fallback_resolution,
            "baseline_game_prior": {
                "attacker": transition.get("selection", {}).get("attacker", {}),
                "defender": {
                    **(transition.get("selection", {}).get("defender", {}) or {}),
                    "canonical_baseline_top": baseline_top_defender,
                },
            },
        }
    )
    decision_trace.update(
        {
            "stage_id": stage_id,
            "decision_source": "llm_defender",
            "strategy_space_checksum": strategy_space_checksum,
            "strategy_mismatch": strategy_mismatch,
            "active_attacker_ids": active["attacker_ids"],
            "active_defender_ids": active["defender_ids"],
            "filtered_out_reasons": active.get("filtered_out_reasons", {}),
            "pre_stage_reset": pre_stage_reset,
            "llm": llm_metadata,
            "stage_validation": validation,
            "stage_summary": stage_summary_record,
            "baseline_top_defender_strategy_id": llm_metadata["baseline_top_defender_strategy_id"],
            "baseline_top_defender_utility": llm_metadata["baseline_top_defender_utility"],
            "baseline_top_defender_population_share": llm_metadata["baseline_top_defender_population_share"],
            "baseline_top_defender_tiebreak_reason": llm_metadata["baseline_top_defender_tiebreak_reason"],
            "llm_selected_defender_strategy_id": llm_metadata["llm_selected_defender_strategy_id"],
            "raw_llm_selected_defender_strategy_id": llm_metadata["raw_llm_selected_defender_strategy_id"],
            "final_defender_strategy_id": llm_metadata["final_defender_strategy_id"],
            "final_defender_action": llm_metadata["final_defender_action"],
            "final_defender_target": llm_metadata["final_defender_target"],
            "executed_via_fallback": llm_metadata["executed_via_fallback"],
            "fallback_reason": llm_metadata["fallback_reason"],
            "raw_llm_reasoning_summary": llm_metadata["raw_llm_reasoning_summary"],
            "executed_decision_reasoning_summary": llm_metadata["executed_decision_reasoning_summary"],
            "llm_ranked_candidates": llm_metadata["llm_ranked_candidates"],
            "llm_baseline_alignment": llm_metadata["llm_baseline_alignment"],
            "raw_llm_alignment_vs_baseline": llm_metadata["raw_llm_alignment_vs_baseline"],
            "final_decision_alignment_vs_baseline": llm_metadata["final_decision_alignment_vs_baseline"],
            "final_baseline_alignment": llm_metadata["final_baseline_alignment"],
            "llm_override_reason": llm_metadata["llm_override_reason"],
            "llm_urgency_level": llm_metadata["llm_urgency_level"],
            "llm_decision_mode": llm_metadata["llm_decision_mode"],
            "llm_telemetry_confidence": llm_metadata["llm_telemetry_confidence"],
            "llm_repeat_previous_action": llm_metadata["llm_repeat_previous_action"],
            "llm_why_not_observe": llm_metadata["llm_why_not_observe"],
            "llm_why_not_rate_limit": llm_metadata["llm_why_not_rate_limit"],
            "llm_why_not_quarantine": llm_metadata["llm_why_not_quarantine"],
            "llm_expected_security_gain": llm_metadata["expected_security_gain"],
            "llm_expected_qos_impact": llm_metadata["expected_qos_impact"],
            "llm_expected_controller_cost": llm_metadata["expected_controller_cost"],
            "llm_stage_memory_used": llm_metadata["llm_stage_memory_used"],
            "fallback_resolution": fallback_resolution,
            "baseline_game_prior": {
                "attacker": decision_trace.get("selection", {}).get("attacker", {}),
                "defender": {
                    **(decision_trace.get("selection", {}).get("defender", {}) or {}),
                    "canonical_baseline_top": baseline_top_defender,
                },
            },
        }
    )

    skip_persistence = modules.strategy_runtime.should_skip_persistence_for_state(stage_state)
    if skip_persistence:
        validation["paper_valid_stage"] = False
        validation["learning_valid_stage"] = False
        validation["llm_stage_valid"] = False
        validation["operationally_executed_debug_stage"] = bool(defender_execution.get("status") == "executed")
        invalidity_reasons = list(validation.get("invalidity_reasons") or [])
        if "persistence_skipped" not in invalidity_reasons:
            invalidity_reasons.append("persistence_skipped")
        validation["invalidity_reasons"] = invalidity_reasons
        stage_summary_record["stage_validation"] = validation
        stage_summary_record.setdefault("execution", {})["stage_valid"] = False
        stage_summary_record["execution"]["operationally_executed_debug_stage"] = validation["operationally_executed_debug_stage"]
        transition["stage_validation"] = validation
        decision_trace["stage_validation"] = validation
    learning_eligible = validation["learning_valid_stage"]
    persistence = {
        "skipped": skip_persistence,
        "population_saved": False,
        "stage_logged": False,
        "decision_trace_logged": False,
        "summary_logged": False,
        "learning_eligible": learning_eligible,
        "valid_stage_for_learning": validation["learning_valid_stage"],
        "paper_valid_stage": validation["paper_valid_stage"],
        "learning_skipped_reason": (
            ""
            if learning_eligible
            else ("unsuccessful_outcome" if validation["paper_valid_stage"] else ",".join(validation["invalidity_reasons"]) or validation["stage_kind"])
        ),
        "reason": (
            "source ingestion failed with zero path stage, empty attack/defense metrics, and all-zero workload"
            if skip_persistence
            else ""
        ),
    }

    if not no_save_population and not skip_persistence and learning_eligible:
        modules.strategy_runtime.save_population(population_file_path, game, {"state": stage_state, "selection": compact_pair})
        persistence["population_saved"] = True
    if not no_stage_log and not skip_persistence:
        modules.stage_transition.append_transition(stage_log_path, transition)
        persistence["stage_logged"] = True
    if not no_decision_trace_log and not skip_persistence:
        modules.stage_transition.append_decision_trace(decision_log_path, decision_trace)
        persistence["decision_trace_logged"] = True
    if not skip_persistence:
        _append_jsonl(summary_log_path, stage_summary_record)
        persistence["summary_logged"] = True

    post_stage_reset = None
    if effective_execute_defender and defender_execution.get("status") == "executed":
        post_stage_reset = modules.strategy_runtime.stage_teardown(
            effective_ryu_action_url,
            effective_mtd_status_url,
            effective_timeout,
            reset_actions=True,
        )

    run_id = _make_run_id(config.model_name(), scenario_id)
    result = {
        "schema_version": "llm-mtd-live-stage-result-v1",
        "run_id": run_id,
        "recorded_at": _utc_now_iso(),
        "decision_source": "llm_defender",
        "strategy_space_checksum": strategy_space_checksum,
        "strategy_mismatch": strategy_mismatch,
        "runtime_reference_strategy_space_checksum": runtime_checksum,
        "state": stage_state,
        "next_state": next_state,
        "active": {
            "attacker_ids": active["attacker_ids"],
            "defender_ids": active["defender_ids"],
            "attackers": [_compact_active_strategy(strategy) for strategy in active["attackers"]],
            "defenders": [_compact_active_strategy(strategy) for strategy in active["defenders"]],
            "filtered_out_reasons": active.get("filtered_out_reasons", {}),
        },
        "game": game,
        "selection": compact_pair,
        "execution": execution,
        "stage_validation": validation,
        "llm": {
            **llm_metadata,
            "raw_response": defender_result.raw_response,
            "prompt_preview": defender_result.trace.prompt_preview,
            "latency_ms": defender_result.trace.latency_ms,
            "retries_used": defender_result.trace.retries_used,
        },
        "fallback_resolution": fallback_resolution,
        "stage_summary": stage_summary_record,
        "transition": transition,
        "decision_trace": decision_trace,
        "persistence": persistence,
        "pre_stage_reset": pre_stage_reset,
        "post_stage_reset": post_stage_reset,
    }

    result_path = config.raw_output_dir / f"{run_id}.json"
    defender_trace_path = config.traces_output_dir / f"{run_id}_defender_trace.json"
    write_json(result_path, result)
    write_json(defender_trace_path, defender_result.trace.model_dump(mode="json"))
    artifacts = {
        "result_path": str(result_path),
        "defender_trace_path": str(defender_trace_path),
        "stage_log": str(stage_log_path),
        "decision_trace_log": str(decision_log_path),
        "summary_log": str(summary_log_path),
        "population_file": str(population_file_path),
    }
    if stage_summary.trace is not None:
        summary_trace_path = config.traces_output_dir / f"{run_id}_summary_trace.json"
        write_json(summary_trace_path, stage_summary.trace.model_dump(mode="json"))
        artifacts["summary_trace_path"] = str(summary_trace_path)

    return {
        "result": result,
        "artifacts": artifacts,
    }
