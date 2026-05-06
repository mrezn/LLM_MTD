#!/usr/bin/env python3
"""Build and store repeated-game stage transition records."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


STRATEGY_DIR = Path(__file__).resolve().parent
DEFAULT_STAGE_LOG = STRATEGY_DIR / "stage_history.jsonl"
DEFAULT_DECISION_TRACE_LOG = STRATEGY_DIR / "decision_trace.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_json_body(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        if isinstance(value.get("json"), dict):
            return value["json"]
        body = value.get("body")
        if isinstance(body, str):
            try:
                parsed = json.loads(body or "{}")
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def compact_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not state:
        return {}
    return {
        "built_at": state.get("built_at"),
        "scenario_id": state.get("scenario_id"),
        "entry_node": state.get("entry_node"),
        "target_asset": state.get("target_asset"),
        "current_path": state.get("current_path", []),
        "path_stage": state.get("path_stage"),
        "path_stage_label": state.get("path_stage_label"),
        "attack_active": state.get("attack_active"),
        "attack_effect_success": state.get("attack_effect_success"),
        "defense_active": state.get("defense_active"),
        "defense_success": state.get("defense_success"),
        "drop_rules_active": state.get("drop_rules_active"),
        "counters_stopped": state.get("counters_stopped"),
        "qos": state.get("qos", {}),
        "workload": state.get("workload", {}),
        "overhead": state.get("overhead", {}),
        "mulval": {
            "current_path_risk": (state.get("mulval") or {}).get("current_path_risk"),
            "plausible_paths": (state.get("mulval") or {}).get("plausible_paths", []),
        },
    }


def selected_id(selection: Dict[str, Any], role: str) -> str:
    item = (selection or {}).get(role) or {}
    return str(item.get("id", ""))


def expected_effects(selection: Dict[str, Any], role: str) -> Any:
    item = (selection or {}).get(role) or {}
    strategy = item.get("strategy") or {}
    return strategy.get("expected_effects") or item.get("expected_effects") or []


def descending_rank(values: Dict[str, Any], selected: str) -> int:
    if not selected or not isinstance(values, dict) or selected not in values:
        return 0
    selected_value = safe_float(values.get(selected))
    higher_values = {
        safe_float(value)
        for key, value in values.items()
        if str(key) != selected and safe_float(value) > selected_value
    }
    return len(higher_values) + 1


def selection_summary(selection: Dict[str, Any], role: str, game: Dict[str, Any]) -> Dict[str, Any]:
    item = dict((selection or {}).get(role) or {})
    if not item:
        return {}

    selected = str(item.get("id", ""))
    population_before = game.get(f"{role}_population_before", {}) or {}
    population_after = game.get(f"{role}_population", {}) or {}
    utilities = game.get(f"{role}_utilities", {}) or {}

    before = safe_float(population_before.get(selected), safe_float(item.get("probability")))
    after = safe_float(population_after.get(selected), safe_float(item.get("probability")))
    utility = safe_float(utilities.get(selected), safe_float(item.get("utility")))

    item["utility"] = utility
    item["population_before"] = before
    item["population_after"] = after
    item["population_delta"] = after - before
    item["population_before_rank"] = descending_rank(population_before, selected)
    item["population_after_rank"] = descending_rank(population_after, selected)
    item["utility_rank"] = descending_rank(utilities, selected)
    return item


def attacker_execution_summary(execution: Dict[str, Any]) -> Dict[str, Any]:
    plan = execution.get("plan") if isinstance(execution.get("plan"), dict) else {}
    post_result = execution.get("post_result") if isinstance(execution.get("post_result"), dict) else {}
    post_body = parse_json_body(post_result)
    return {
        "status": execution.get("status", ""),
        "dispatch_url": post_result.get("url", ""),
        "dispatch_http_status": safe_int(post_result.get("status")),
        "dispatch_ok": bool(post_result.get("ok")),
        "strategy_id": plan.get("strategy_id", ""),
        "strategy_name": plan.get("strategy_name", ""),
        "live_attack_type": plan.get("live_attack_type", ""),
        "attempted_path": plan.get("path", []) or [],
        "expected_effects": plan.get("expected_effects", []) or [],
        "operation_id": str(post_body.get("operation_id") or post_body.get("id") or ""),
    }


def defender_execution_summary(execution: Dict[str, Any]) -> Dict[str, Any]:
    payload = execution.get("payload") if isinstance(execution.get("payload"), dict) else {}
    policy_context = execution.get("cloud_policy_context") if isinstance(
        execution.get("cloud_policy_context"), dict
    ) else {}
    policy_context_body = parse_json_body(policy_context)
    policy_decision = execution.get("cloud_policy_decision") if isinstance(
        execution.get("cloud_policy_decision"), dict
    ) else {}
    policy_decision_body = parse_json_body(policy_decision)
    ryu_post = execution.get("post_result") if isinstance(execution.get("post_result"), dict) else {}
    ryu_body = parse_json_body(ryu_post)
    defense_event = execution.get("defense_event") if isinstance(execution.get("defense_event"), dict) else {}
    defense_event_post = defense_event.get("post_result") if isinstance(
        defense_event.get("post_result"), dict
    ) else {}

    return {
        "status": execution.get("status", ""),
        "action": payload.get("action", ""),
        "target": payload.get("target", ""),
        "cloud_policy": {
            "context_http_status": safe_int(policy_context.get("status")),
            "context_ok": bool(policy_context.get("ok")),
            "accepted_keys": policy_context_body.get("accepted_keys", []) or [],
            "decision_http_status": safe_int(policy_decision.get("status")),
            "decision_ok": bool(policy_decision.get("ok")),
            "observe_only": policy_decision_body.get("observe_only"),
            "selected_action": policy_decision_body.get("selected_action") or {},
            "external_context": policy_decision_body.get("external_context") or {},
        },
        "ryu_response": {
            "http_status": safe_int(ryu_post.get("status")),
            "ok": bool(ryu_post.get("ok")),
            "status": ryu_body.get("status", ""),
            "action": ryu_body.get("action", payload.get("action", "")),
            "target": ryu_body.get("target", payload.get("target", "")),
            "rule": ryu_body.get("rule", ""),
            "active_policy_actions": safe_int(ryu_body.get("active_policy_actions")),
            "flow_rules_installed": safe_int(ryu_body.get("flow_rules_installed")),
            "flow_delete_commands": safe_int(ryu_body.get("flow_delete_commands")),
            "meters_added": safe_int(ryu_body.get("meters_added")),
            "apply_duration_ms": safe_float(ryu_body.get("ryu_apply_duration_ms")),
            "target_ips": ryu_body.get("target_ips", []) or [],
        },
        "defense_event": {
            "status": defense_event.get("status", ""),
            "http_status": safe_int(defense_event_post.get("status")),
            "ok": bool(defense_event_post.get("ok")),
            "url": defense_event_post.get("url", ""),
            "signals": ((defense_event.get("payload") or {}).get("signals") or {}),
        },
    }


def state_summary(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not state:
        return {}
    observation = state.get("defender_observation") or {}
    overhead = state.get("overhead") or {}
    return {
        "scenario_id": state.get("scenario_id"),
        "path_stage": safe_int(state.get("path_stage")),
        "path_stage_label": state.get("path_stage_label", ""),
        "gateway_seen": bool(observation.get("gateway_seen")),
        "worker_seen": bool(observation.get("worker_seen")),
        "cloud_seen": bool(observation.get("cloud_seen")),
        "attack_active": bool(state.get("attack_active")),
        "attack_success": bool(state.get("attack_success")),
        "attack_effect_success": bool(state.get("attack_effect_success")),
        "defense_active": bool(state.get("defense_active")),
        "defense_success": bool(state.get("defense_success")),
        "drop_rules_active": bool(state.get("drop_rules_active")),
        "counters_stopped": bool(state.get("counters_stopped")),
        "controller_active_actions": safe_int(overhead.get("controller_active_actions")),
        "flow_rules_installed": safe_int(overhead.get("flow_rules_installed")),
        "meters_added": safe_int(overhead.get("meters_added")),
    }


def infer_transition(
    previous_state: Dict[str, Any],
    next_state: Optional[Dict[str, Any]],
    selection: Dict[str, Any],
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    previous_stage = safe_int(previous_state.get("path_stage"))
    next_stage = safe_int((next_state or previous_state).get("path_stage"))
    defender_status = (execution.get("defender") or {}).get("status", "")
    attacker_status = (execution.get("attacker") or {}).get("status", "")

    next_effective = next_state or {}
    return {
        "attacker_strategy_id": selected_id(selection, "attacker"),
        "defender_strategy_id": selected_id(selection, "defender"),
        "attacker_execution_status": attacker_status,
        "defender_execution_status": defender_status,
        "path_stage_delta": next_stage - previous_stage,
        "attacker_progressed": next_stage > previous_stage,
        "path_frozen": next_stage == previous_stage and bool(previous_state.get("attack_active")),
        "defense_applied": defender_status == "executed",
        "defense_observed": defender_status == "observe_only",
        "defense_containment_observed": bool(
            next_effective.get("counters_stopped")
            or next_effective.get("drop_rules_active")
            or next_effective.get("defense_success")
        ),
        "attacker_expected_effects": expected_effects(selection, "attacker"),
        "defender_expected_effects": expected_effects(selection, "defender"),
    }


def build_transition_record(
    previous_state: Dict[str, Any],
    next_state: Optional[Dict[str, Any]],
    selection: Dict[str, Any],
    execution: Dict[str, Any],
    game: Dict[str, Any],
) -> Dict[str, Any]:
    transition_id = uuid.uuid4().hex
    return {
        "schema_version": "llm-mtd-stage-transition-v2",
        "transition_id": transition_id,
        "recorded_at": utc_now_iso(),
        "previous_state": compact_state(previous_state),
        "next_state": compact_state(next_state),
        "selection": {
            "attacker": selection_summary(selection, "attacker", game),
            "defender": selection_summary(selection, "defender", game),
        },
        "population_before": {
            "attacker": game.get("attacker_population_before", {}),
            "defender": game.get("defender_population_before", {}),
        },
        "population_after": {
            "attacker": game.get("attacker_population", {}),
            "defender": game.get("defender_population", {}),
        },
        "average_utility": {
            "attacker": game.get("attacker_average_utility"),
            "defender": game.get("defender_average_utility"),
        },
        "execution": execution,
        "execution_summary": {
            "attacker": attacker_execution_summary((execution or {}).get("attacker") or {}),
            "defender": defender_execution_summary((execution or {}).get("defender") or {}),
        },
        "state_summary": state_summary(next_state or previous_state),
        "inference": infer_transition(previous_state, next_state, selection, execution),
    }


def build_decision_trace_record(
    previous_state: Dict[str, Any],
    next_state: Optional[Dict[str, Any]],
    selection: Dict[str, Any],
    execution: Dict[str, Any],
    game: Dict[str, Any],
    transition_id: str = "",
) -> Dict[str, Any]:
    return {
        "schema_version": "llm-mtd-decision-trace-v1",
        "transition_id": transition_id or uuid.uuid4().hex,
        "recorded_at": utc_now_iso(),
        "scenario_id": (next_state or previous_state or {}).get("scenario_id", ""),
        "selection": {
            "attacker": selection_summary(selection, "attacker", game),
            "defender": selection_summary(selection, "defender", game),
        },
        "execution": {
            "attacker": attacker_execution_summary((execution or {}).get("attacker") or {}),
            "defender": defender_execution_summary((execution or {}).get("defender") or {}),
        },
        "state_summary": state_summary(next_state or previous_state),
        "average_utility": {
            "attacker": game.get("attacker_average_utility"),
            "defender": game.get("defender_average_utility"),
        },
    }


def append_record(path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    stored = dict(record)
    stage_id = 1
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            stage_id = sum(1 for _ in handle) + 1
    stored.setdefault("stage_id", stage_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stored, sort_keys=True) + "\n")
    return stored


def append_transition(path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    return append_record(path, record)


def append_decision_trace(path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    return append_record(path, record)
