from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any

from eval.types import LLMResponseTrace
from .llm_client import LLMClient
from .response_parser import extract_first_json_object


@dataclass(slots=True)
class DefenderSelectorResult:
    selection: dict[str, Any]
    trace: LLMResponseTrace
    raw_response: dict[str, Any]
    fallback_used: bool
    fallback_reason: str
    request_success: bool
    request_error: str
    parse_success: bool
    recovery_used: bool
    ranked_candidates: list[dict[str, Any]]
    baseline_alignment: str
    override_reason: str
    urgency_level: str
    stage_memory_used: bool
    baseline_top_strategy_id: str
    baseline_top_utility: float
    raw_selected_strategy_id: str
    raw_baseline_alignment: str
    raw_override_reason: str
    raw_reasoning_summary: str
    raw_ranked_candidates: list[dict[str, Any]]
    executed_via_fallback: bool
    executed_decision_source: str
    fallback_constraint_name: str
    decision_mode: str
    telemetry_confidence: str
    repeat_previous_action: bool
    why_not_observe: str
    why_not_rate_limit: str
    why_not_quarantine: str


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


def _safe_bool(value: Any, default: bool = False) -> bool:
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
    return default


def select_baseline_top_defender(
    active_defenders: list[dict[str, Any]],
    game_result: dict[str, Any],
) -> dict[str, Any]:
    defender_utilities = dict(game_result.get("defender_utilities") or {})
    defender_population = dict(game_result.get("defender_population") or {})
    if not active_defenders:
        return {
            "strategy_id": "",
            "utility": 0.0,
            "population_share": 0.0,
            "base_cost": 0.0,
            "tiebreak_reason": "no_active_defender",
        }

    ranked = sorted(
        active_defenders,
        key=lambda item: (
            -_safe_float(defender_utilities.get(str(item.get("id") or ""), 0.0)),
            -_safe_float(defender_population.get(str(item.get("id") or ""), 0.0)),
            _safe_float(item.get("base_cost"), 0.0),
            str(item.get("id") or ""),
        ),
    )
    selected = ranked[0]
    strategy_id = str(selected.get("id") or "")
    utility = _safe_float(defender_utilities.get(strategy_id), 0.0)
    population_share = _safe_float(defender_population.get(strategy_id), 0.0)
    base_cost = _safe_float(selected.get("base_cost"), 0.0)

    top_utility = max(_safe_float(defender_utilities.get(str(item.get("id") or ""), 0.0)) for item in active_defenders)
    top_utility_ids = [
        str(item.get("id") or "")
        for item in active_defenders
        if _safe_float(defender_utilities.get(str(item.get("id") or ""), 0.0)) == top_utility
    ]
    if len(top_utility_ids) == 1:
        tiebreak_reason = "highest_utility"
    else:
        top_population = max(_safe_float(defender_population.get(item_id), 0.0) for item_id in top_utility_ids)
        top_population_ids = [
            item_id
            for item_id in top_utility_ids
            if _safe_float(defender_population.get(item_id), 0.0) == top_population
        ]
        if len(top_population_ids) == 1:
            tiebreak_reason = "utility_tie_highest_population_share"
        else:
            base_costs = {
                str(item.get("id") or ""): _safe_float(item.get("base_cost"), 0.0)
                for item in active_defenders
            }
            lowest_cost = min(base_costs[item_id] for item_id in top_population_ids)
            lowest_cost_ids = [item_id for item_id in top_population_ids if base_costs[item_id] == lowest_cost]
            if len(lowest_cost_ids) == 1:
                tiebreak_reason = "utility_population_tie_lowest_base_cost"
            else:
                tiebreak_reason = "utility_population_cost_tie_lexicographic_strategy_id"
    return {
        "strategy_id": strategy_id,
        "utility": utility,
        "population_share": population_share,
        "base_cost": base_cost,
        "tiebreak_reason": tiebreak_reason,
    }


def _normalize_ranked_candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            strategy_id = str(item.get("strategy_id") or item.get("id") or "").strip()
            if not strategy_id:
                continue
            row = dict(item)
            row.setdefault("strategy_id", strategy_id)
            row.setdefault("llm_rank", index)
            rows.append(row)
            continue
        strategy_id = str(item or "").strip()
        if strategy_id:
            rows.append({"strategy_id": strategy_id, "llm_rank": index})
    return rows


def _extract_strategy_id(text: str) -> str:
    match = re.search(
        r"(?:selected_defender_strategy_id|strategy_id|selected_defender)[\"']?\s*[:=]?\s*[\"']?(D[A-Za-z0-9_]+)",
        text,
    )
    return match.group(1) if match else ""


def _recover_payload(raw_text: str) -> dict[str, Any]:
    strategy_id = _extract_strategy_id(raw_text)
    ranked_candidates = _normalize_ranked_candidates(re.findall(r"\bD[A-Za-z0-9_]+\b", raw_text))
    if not strategy_id and ranked_candidates:
        strategy_id = str(ranked_candidates[0].get("strategy_id") or "")
    if not strategy_id:
        raise ValueError("could not recover defender strategy id")
    return {
        "selected_defender_strategy_id": strategy_id,
        "ranked_candidates": ranked_candidates or [{"strategy_id": strategy_id, "llm_rank": 1}],
        "baseline_alignment": "",
        "override_reason": "",
        "urgency_level": "",
        "confidence": 0.0,
        "reasoning_summary": "Recovered a defender choice from malformed LLM output.",
        "expected_security_gain": 0.0,
        "expected_qos_impact": 0.0,
        "expected_controller_cost": 0.0,
    }


def _parse_payload(raw_text: str) -> dict[str, Any]:
    payload = json.loads(extract_first_json_object(raw_text))
    if not isinstance(payload, dict):
        raise ValueError("defender selector response was not a JSON object")
    return payload


def _strategy_from_action(
    *,
    action_name: str,
    target: str,
    active_defenders: list[dict[str, Any]],
) -> str:
    if not action_name:
        return ""
    exact_target = next(
        (
            str(item.get("id") or "")
            for item in active_defenders
            if str(item.get("action") or "") == action_name and str(item.get("target") or "") == target
        ),
        "",
    )
    if exact_target:
        return exact_target
    return next(
        (
            str(item.get("id") or "")
            for item in active_defenders
            if str(item.get("action") or "") == action_name
        ),
        "",
    )


def _all_zero_mapping(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return True
    return all(_safe_float(item, 0.0) == 0.0 for item in value.values())


def _telemetry_quality(live_state: dict[str, Any]) -> dict[str, Any]:
    observation = dict(live_state.get("defender_observation") or {})
    workload = dict(live_state.get("workload") or {})
    source_errors = list(live_state.get("source_errors") or [])
    attack_metrics = dict(observation.get("attack_metrics") or {})
    defense_metrics = dict(observation.get("defense_metrics") or {})
    reasons: list[str] = []

    source_errors_present = bool(source_errors)
    empty_attack_metrics = not bool(attack_metrics)
    empty_defense_metrics = not bool(defense_metrics)
    all_zero_workload = _all_zero_mapping(workload)

    if source_errors_present:
        reasons.append("source_errors_present")
    if empty_attack_metrics:
        reasons.append("empty_attack_metrics")
    if empty_defense_metrics:
        reasons.append("empty_defense_metrics")
    if all_zero_workload:
        reasons.append("all_zero_workload")

    if source_errors_present or (all_zero_workload and (empty_attack_metrics or empty_defense_metrics)):
        level = "low"
        state_freshness = "stale_or_partial"
    elif reasons:
        level = "medium"
        state_freshness = "partial"
    else:
        level = "high"
        state_freshness = "fresh"

    return {
        "level": level,
        "reasons": reasons,
        "source_errors_present": source_errors_present,
        "source_errors": source_errors,
        "empty_attack_metrics": empty_attack_metrics,
        "empty_defense_metrics": empty_defense_metrics,
        "all_zero_workload": all_zero_workload,
        "state_freshness": state_freshness,
    }


def _previous_defense_failed_same_action(
    *,
    stage_memory: dict[str, Any] | None,
    active_defenders: list[dict[str, Any]],
) -> dict[str, bool]:
    memory = stage_memory or {}
    previous_strategy_id = str(memory.get("previous_defender_strategy_id") or "")
    previous_success = bool(memory.get("previous_defense_success"))
    if not previous_strategy_id or previous_success:
        return {str(item.get("id") or ""): False for item in active_defenders}
    return {str(item.get("id") or ""): str(item.get("id") or "") == previous_strategy_id for item in active_defenders}


def _candidate_record(
    defender: dict[str, Any],
    *,
    utility: float,
    population_share: float,
    path_nodes: set[str],
    attack_active: bool,
    path_stage: int,
    previous_failed_same_action: bool,
    max_fields: int | None,
) -> dict[str, Any]:
    action = str(defender.get("action") or "")
    target = str(defender.get("target") or "")
    target_on_path = bool(target and target in path_nodes)
    security_gain = 0.2
    qos_impact = 0.04
    controller_cost = 0.02
    if action in {"quarantine_sensor", "isolate_sensor"}:
        security_gain = 0.82 if attack_active and path_stage >= 2 else 0.68
        qos_impact = 0.32
        controller_cost = 0.22
    elif action == "rate_limit":
        security_gain = 0.58
        qos_impact = 0.16
        controller_cost = 0.12
    elif action == "reroute_traffic":
        security_gain = 0.46
        qos_impact = 0.14
        controller_cost = 0.1
    elif action == "observe":
        security_gain = 0.1 if attack_active and path_stage >= 2 else 0.26
        qos_impact = 0.01
        controller_cost = 0.0
    record = {
        "strategy_id": str(defender.get("id") or ""),
        "action": action,
        "target": target,
        "baseline_utility_prior": round(utility, 6),
        "defender_population_share": round(population_share, 6),
        "expected_security_gain": round(security_gain, 6),
        "expected_qos_impact": round(qos_impact, 6),
        "expected_controller_cost": round(controller_cost, 6),
        "path_relevance": 1.0 if target_on_path else (0.2 if action == "observe" else 0.0),
        "path_stage_compatibility": 0.15 if action == "observe" and attack_active and path_stage >= 2 else 0.9,
        "previous_failed_same_action": previous_failed_same_action,
    }
    if max_fields is not None and max_fields > 0:
        keys = list(record.keys())[:max_fields]
        return {key: record[key] for key in keys}
    return record


def _build_prompt_payload(
    *,
    live_state: dict[str, Any],
    selected_attacker: dict[str, Any],
    attacker_execution: dict[str, Any] | None,
    active_defenders: list[dict[str, Any]],
    game_result: dict[str, Any],
    stage_memory: dict[str, Any] | None,
    max_candidate_fields: int | None,
    prompt_mode: str,
) -> dict[str, Any]:
    defender_population = dict(game_result.get("defender_population") or {})
    defender_utilities = dict(game_result.get("defender_utilities") or {})
    current_path = live_state.get("current_path") or selected_attacker.get("path") or []
    path_nodes = {str(item) for item in current_path if item not in ("", None)}
    qos = dict(live_state.get("qos") or {})
    overhead = dict(live_state.get("overhead") or {})
    observation = dict(live_state.get("defender_observation") or {})
    attack_active = bool(live_state.get("attack_active"))
    path_stage = _safe_int(live_state.get("path_stage"))
    gateway_seen = bool(live_state.get("gateway_seen") or observation.get("gateway_seen"))
    worker_seen = bool(live_state.get("worker_seen") or observation.get("worker_seen"))
    cloud_seen = bool(live_state.get("cloud_seen") or observation.get("cloud_seen"))
    attack_effect_success = bool(live_state.get("attack_effect_success"))
    telemetry_quality = _telemetry_quality(live_state)
    previous_failed_same_action = _previous_defense_failed_same_action(
        stage_memory=stage_memory,
        active_defenders=active_defenders,
    )
    return {
        "prompt_mode": prompt_mode,
        "scenario_id": live_state.get("scenario_id", ""),
        "entry_node": live_state.get("entry_node", ""),
        "target_asset": live_state.get("target_asset", ""),
        "allowed_defender_ids": [str(item.get("id") or "") for item in active_defenders],
        "current_evidence": {
            "attack_active": attack_active,
            "attack_effect_success": attack_effect_success,
            "gateway_seen": gateway_seen,
            "worker_seen": worker_seen,
            "cloud_seen": cloud_seen,
            "path_stage": path_stage,
            "path_stage_label": live_state.get("path_stage_label", ""),
            "attack_dispatch_status": (attacker_execution or {}).get("status", ""),
        },
        "telemetry_quality": telemetry_quality,
        "attack_context": {
            "attack_active": attack_active,
            "path_stage": path_stage,
            "current_path": current_path,
            "attack_effect_success": attack_effect_success,
            "gateway_seen": gateway_seen,
            "worker_seen": worker_seen,
            "cloud_seen": cloud_seen,
            "selected_attacker_strategy_id": selected_attacker.get("id", ""),
            "selected_attacker_path": selected_attacker.get("path") or current_path,
            "attacker_execution_status": (attacker_execution or {}).get("status", ""),
        },
        "qos_context": {
            "sensor_to_edge_latency_ms": _safe_float(qos.get("sensor_to_edge_latency_ms")),
            "edge_to_cloud_latency_ms": _safe_float(qos.get("edge_to_cloud_latency_ms")),
            "loss_rate": _safe_float(qos.get("loss_rate")),
            "throughput_bytes_per_second": _safe_float((live_state.get("workload") or {}).get("throughput_bytes_per_second")),
        },
        "controller_context": {
            "controller_active_actions": _safe_int(overhead.get("controller_active_actions")),
            "flow_rules_installed": _safe_int(overhead.get("flow_rules_installed")),
            "meters_added": _safe_int(overhead.get("meters_added")),
            "controller_apply_ms": _safe_float(overhead.get("controller_apply_ms")),
        },
        "previous_stage_memory": stage_memory or {},
        "baseline_top_defender": select_baseline_top_defender(active_defenders, game_result),
        "active_defender_candidates": [
            _candidate_record(
                defender,
                utility=_safe_float(defender_utilities.get(str(defender.get("id") or ""), 0.0)),
                population_share=_safe_float(defender_population.get(str(defender.get("id") or ""), 0.0)),
                path_nodes=path_nodes,
                attack_active=attack_active,
                path_stage=path_stage,
                previous_failed_same_action=previous_failed_same_action.get(str(defender.get("id") or ""), False),
                max_fields=max_candidate_fields,
            )
            for defender in active_defenders
        ],
        "required_schema": {
            "selected_defender_strategy_id": "string",
            "ranked_candidates": "list",
            "reasoning_summary": "string",
            "decision_mode": "reactive_containment|proactive_hardening|uncertainty_limited",
            "telemetry_confidence": "high|medium|low",
            "baseline_alignment": "followed|overrode",
            "override_reason": "string",
            "repeat_previous_action": "boolean",
            "why_not_observe": "string",
            "why_not_rate_limit": "string",
            "why_not_quarantine": "string",
            "urgency_level": "low|medium|high|critical",
            "confidence": "number",
            "expected_security_gain": "number",
            "expected_qos_impact": "number",
            "expected_controller_cost": "number",
        },
    }


def _build_prompts(payload: dict[str, Any]) -> tuple[str, str]:
    allowed_ids = payload.get("allowed_defender_ids") or []
    allowed_ids_str = ", ".join(str(i) for i in allowed_ids)
    if str(payload.get("prompt_mode") or "full") == "compact":
        system_prompt = (
            "You select one defender action from allowed defender IDs for a live edge-cloud MTD system. "
            f"CRITICAL: You MUST set selected_defender_strategy_id to EXACTLY one of: [{allowed_ids_str}]. "
            "Any other ID (including D3, D4, or IDs from previous stages) is INVALID and will be rejected. "
            "Choose the least disruptive sufficient defense. If attack_effect_success=true, attack_active=true, "
            "or path_stage>=2, prefer containment over observe. If attack_active=false and gateway_seen=false "
            "and worker_seen=false and cloud_seen=false, prefer observe or rate-limit unless stronger action is "
            "clearly justified. If telemetry is stale, source_errors are present, or workload is all zero, reduce "
            "confidence and avoid aggressive containment based only on path relevance. Do not repeat the same "
            "failed defense unless current evidence is stronger. Return valid JSON only."
        )
    else:
        system_prompt = (
            "You are the defender policy reasoner for a live edge-cloud moving target defense system. Select exactly "
            "one defender strategy from the allowed active defender candidates. "
            f"CRITICAL CONSTRAINT: selected_defender_strategy_id MUST be exactly one of: [{allowed_ids_str}]. "
            "Any strategy ID not in this list (including D3, D4, or IDs from previous-stage memory) is NOT "
            "available in the current game state and will be rejected — do NOT use them. "
            "Base your decision on current_evidence (attack_active, path_stage) — NOT on previous_stage_memory "
            "alone. previous_stage_memory is supporting context only; the previous defender strategy ID may refer "
            "to a strategy that is inactive in the current state. "
            "Balance current verified threat evidence, path-stage urgency, expected security gain, QoS impact, "
            "controller cost, previous failed defenses, telemetry confidence, and state freshness. "
            "Attack dispatched alone is not attack active; path relevance alone is not proof of ongoing compromise; "
            "zero workload plus source errors means the state may be stale or partially unavailable. "
            "Distinguish reactive_containment, proactive_hardening, and uncertainty_limited responses. "
            "Prefer the least disruptive sufficient action when threat evidence is weak, stale, or contradictory. "
            "Return valid JSON only with the required schema."
        )
    prompt_mode = str(payload.get("prompt_mode") or "full")
    user_prompt = f"Prompt mode: {prompt_mode}\n\n" + json.dumps(payload, indent=2, sort_keys=True)
    return system_prompt, user_prompt


def _make_trace(client: LLMClient, user_prompt: str, started_at: float) -> LLMResponseTrace:
    return LLMResponseTrace(
        provider=client.provider,
        model_name=client.model_name,
        raw_text="",
        latency_ms=max((time.monotonic() - started_at) * 1000.0, 0.0),
        retries_used=0,
        prompt_preview=user_prompt[:240],
    )


def _selection_from_strategy(
    strategy: dict[str, Any],
    game_result: dict[str, Any],
    *,
    mode: str,
    confidence: float,
    reasoning_summary: str,
    expected_security_gain: float,
    expected_qos_impact: float,
    expected_controller_cost: float,
    baseline_alignment: str,
    override_reason: str,
    urgency_level: str,
    stage_memory_used: bool,
    decision_mode: str,
    telemetry_confidence: str,
    repeat_previous_action: bool,
    why_not_observe: str,
    why_not_rate_limit: str,
    why_not_quarantine: str,
) -> dict[str, Any]:
    strategy_id = str(strategy.get("id") or "")
    return {
        "id": strategy_id,
        "name": strategy.get("name", ""),
        "probability": _safe_float((game_result.get("defender_population") or {}).get(strategy_id), 0.0),
        "utility": _safe_float((game_result.get("defender_utilities") or {}).get(strategy_id), 0.0),
        "mode": mode,
        "strategy": strategy,
        "scenario_id": strategy.get("scenario_id"),
        "action": strategy.get("action"),
        "target": strategy.get("target"),
        "expected_effects": strategy.get("expected_effects", []) or [],
        "action_payload": strategy.get("action_payload"),
        "confidence": confidence,
        "reasoning_summary": reasoning_summary,
        "expected_security_gain": expected_security_gain,
        "expected_qos_impact": expected_qos_impact,
        "expected_controller_cost": expected_controller_cost,
        "baseline_alignment": baseline_alignment,
        "override_reason": override_reason,
        "urgency_level": urgency_level,
        "stage_memory_used": stage_memory_used,
        "decision_mode": decision_mode,
        "telemetry_confidence": telemetry_confidence,
        "repeat_previous_action": repeat_previous_action,
        "why_not_observe": why_not_observe,
        "why_not_rate_limit": why_not_rate_limit,
        "why_not_quarantine": why_not_quarantine,
    }


def _choose_fallback_strategy(active_defenders: list[dict[str, Any]], live_state: dict[str, Any]) -> dict[str, Any]:
    current_path = {str(item) for item in (live_state.get("current_path") or []) if item not in ("", None)}
    ranked = sorted(
        active_defenders,
        key=lambda item: (
            0 if str(item.get("action") or "") in {"isolate_sensor", "quarantine_sensor"} else 1,
            0 if str(item.get("target") or "") in current_path else 1,
            _safe_float(item.get("base_cost"), 0.0),
            str(item.get("id") or ""),
        ),
    )
    return ranked[0] if ranked else {}


def select_defender_strategy(
    *,
    llm_config: dict[str, Any],
    live_state: dict[str, Any],
    selected_attacker: dict[str, Any],
    active_defenders: list[dict[str, Any]],
    game_result: dict[str, Any],
    attacker_execution: dict[str, Any] | None = None,
    stage_memory: dict[str, Any] | None = None,
    llm_timeout_seconds: float | None = None,
    llm_max_retries: int | None = None,
    llm_compact_prompt: bool = False,
    llm_max_candidate_fields: int | None = None,
) -> DefenderSelectorResult:
    config = dict(llm_config)
    if llm_timeout_seconds is not None:
        config["timeout_seconds"] = llm_timeout_seconds
    if llm_max_retries is not None:
        config["max_retries"] = llm_max_retries
    config.setdefault("strict_json", True)
    client = LLMClient(config)
    started_at = time.monotonic()
    baseline_top = select_baseline_top_defender(active_defenders, game_result)

    full_payload = _build_prompt_payload(
        live_state=live_state,
        selected_attacker=selected_attacker,
        attacker_execution=attacker_execution,
        active_defenders=active_defenders,
        game_result=game_result,
        stage_memory=stage_memory,
        max_candidate_fields=llm_max_candidate_fields,
        prompt_mode="full",
    )
    system_prompt, user_prompt = _build_prompts(full_payload)

    trace: LLMResponseTrace | None = None
    raw_payload: dict[str, Any] = {}
    request_success = False
    parse_success = False
    recovery_used = False
    request_error = ""

    try:
        trace = client.complete_json(system_prompt, user_prompt, state=None)
        request_success = True
    except Exception as error:
        request_error = f"{type(error).__name__}:{error}"
        timed_out = "timed out" in str(error).lower()
        if timed_out:
            compact_payload = _build_prompt_payload(
                live_state=live_state,
                selected_attacker=selected_attacker,
                attacker_execution=attacker_execution,
                active_defenders=active_defenders,
                game_result=game_result,
                stage_memory=stage_memory,
                max_candidate_fields=llm_max_candidate_fields,
                prompt_mode="compact",
            )
            compact_system_prompt, compact_user_prompt = _build_prompts(compact_payload)
            try:
                trace = client.complete_json(compact_system_prompt, compact_user_prompt, state=None)
                request_success = True
                request_error = ""
            except Exception as compact_error:
                request_error = f"{type(compact_error).__name__}:{compact_error}"
                trace = _make_trace(client, compact_user_prompt, started_at)
        else:
            trace = _make_trace(client, user_prompt, started_at)

    if request_success and trace is not None:
        try:
            raw_payload = _parse_payload(trace.raw_text)
            parse_success = True
        except Exception as parse_error:
            request_error = request_error or f"{type(parse_error).__name__}:{parse_error}"

    if request_success and trace is not None and not parse_success:
        try:
            raw_payload = _parse_payload(trace.raw_text)
            parse_success = True
        except Exception as parse_error:
            try:
                raw_payload = _recover_payload(trace.raw_text)
                recovery_used = True
            except Exception:
                raw_payload = {}
                request_error = request_error or f"{type(parse_error).__name__}:{parse_error}"

    trace = trace or _make_trace(client, user_prompt, started_at)

    raw_selected_strategy_id = str(raw_payload.get("selected_defender_strategy_id") or "").strip()
    if not raw_selected_strategy_id:
        raw_selected_strategy_id = _strategy_from_action(
            action_name=str(raw_payload.get("selected_defender_strategy") or "").strip(),
            target=str(raw_payload.get("target") or "").strip(),
            active_defenders=active_defenders,
        )
    raw_ranked_candidates = _normalize_ranked_candidates(raw_payload.get("ranked_candidates"))
    if not raw_ranked_candidates and raw_selected_strategy_id:
        raw_ranked_candidates = [{"strategy_id": raw_selected_strategy_id, "llm_rank": 1}]

    # --- Recovery: map non-active strategy IDs to the closest active equivalent ---
    active_ids = {str(item.get("id") or "") for item in active_defenders}
    if raw_selected_strategy_id and raw_selected_strategy_id not in active_ids:
        # 1. Check ranked_candidates for a valid active ID
        for cand in raw_ranked_candidates:
            cand_id = str(cand.get("strategy_id") or "").strip()
            if cand_id in active_ids:
                raw_selected_strategy_id = cand_id
                break
        else:
            # 2. Map by action type: isolate/quarantine → quarantine or rate_limit in active set
            _ISOLATION_ACTIONS = {"isolate_sensor", "quarantine_sensor"}
            _RATELIMIT_ACTIONS = {"rate_limit", "reroute_traffic"}
            # find what action the LLM wanted (from raw_payload or by matching ID in full space)
            raw_action = str(raw_payload.get("selected_defender_strategy") or "").strip()
            if not raw_action:
                # infer from ranked_candidates action field
                for cand in raw_ranked_candidates:
                    raw_action = str(cand.get("action") or "").strip()
                    if raw_action:
                        break
            if raw_action in _ISOLATION_ACTIONS:
                # prefer quarantine_sensor in active pool, else rate_limit
                raw_selected_strategy_id = next(
                    (str(item.get("id") or "") for item in active_defenders
                     if str(item.get("action") or "") == "quarantine_sensor"),
                    next(
                        (str(item.get("id") or "") for item in active_defenders
                         if str(item.get("action") or "") in _ISOLATION_ACTIONS | _RATELIMIT_ACTIONS),
                        raw_selected_strategy_id,
                    ),
                )
            elif raw_action in _RATELIMIT_ACTIONS:
                raw_selected_strategy_id = next(
                    (str(item.get("id") or "") for item in active_defenders
                     if str(item.get("action") or "") in _RATELIMIT_ACTIONS),
                    raw_selected_strategy_id,
                )

    # --- Recovery: empty strategy ID — scan ranked_candidates for first valid active ID ---
    if not raw_selected_strategy_id:
        for cand in raw_ranked_candidates:
            cand_id = str(cand.get("strategy_id") or "").strip()
            if cand_id in active_ids:
                raw_selected_strategy_id = cand_id
                break
    raw_baseline_alignment = str(raw_payload.get("baseline_alignment") or "").strip()
    if not raw_baseline_alignment:
        raw_baseline_alignment = (
            "followed"
            if raw_selected_strategy_id and raw_selected_strategy_id == str(baseline_top.get("strategy_id") or "")
            else "overrode"
        )
    raw_override_reason = str(raw_payload.get("override_reason") or "").strip()
    raw_reasoning_summary = str(raw_payload.get("reasoning_summary") or "").strip()
    urgency_level = str(raw_payload.get("urgency_level") or "").strip()
    confidence = _safe_float(raw_payload.get("confidence"), 0.0)
    expected_security_gain = _safe_float(raw_payload.get("expected_security_gain"), 0.0)
    expected_qos_impact = _safe_float(raw_payload.get("expected_qos_impact"), 0.0)
    expected_controller_cost = _safe_float(raw_payload.get("expected_controller_cost"), 0.0)
    prompt_telemetry_quality = full_payload.get("telemetry_quality") if isinstance(full_payload.get("telemetry_quality"), dict) else {}
    decision_mode = str(raw_payload.get("decision_mode") or "").strip() or "uncertainty_limited"
    telemetry_confidence = (
        str(raw_payload.get("telemetry_confidence") or "").strip()
        or str((prompt_telemetry_quality or {}).get("level") or "")
        or "medium"
    )
    repeat_previous_action = _safe_bool(raw_payload.get("repeat_previous_action"), False)
    why_not_observe = str(raw_payload.get("why_not_observe") or "").strip()
    why_not_rate_limit = str(raw_payload.get("why_not_rate_limit") or "").strip()
    why_not_quarantine = str(raw_payload.get("why_not_quarantine") or "").strip()

    stage_memory_used = bool(stage_memory)
    selected_strategy = next(
        (item for item in active_defenders if str(item.get("id") or "") == raw_selected_strategy_id),
        None,
    )

    if not request_success:
        fallback_selection = _selection_from_strategy(
            next(
                (item for item in active_defenders if str(item.get("id") or "") == str(baseline_top.get("strategy_id") or "")),
                active_defenders[0] if active_defenders else {},
            ),
            game_result,
            mode="llm_defender_recovered",
            confidence=0.0,
            reasoning_summary="Fallback defender was selected because the LLM request failed.",
            expected_security_gain=0.0,
            expected_qos_impact=0.0,
            expected_controller_cost=0.0,
            baseline_alignment=raw_baseline_alignment,
            override_reason=raw_override_reason,
            urgency_level=urgency_level,
            stage_memory_used=stage_memory_used,
            decision_mode=decision_mode,
            telemetry_confidence=telemetry_confidence,
            repeat_previous_action=repeat_previous_action,
            why_not_observe=why_not_observe,
            why_not_rate_limit=why_not_rate_limit,
            why_not_quarantine=why_not_quarantine,
        )
        return DefenderSelectorResult(
            selection=fallback_selection,
            trace=trace,
            raw_response=raw_payload,
            fallback_used=True,
            fallback_reason=request_error or "llm_request_failed",
            request_success=False,
            request_error=request_error,
            parse_success=False,
            recovery_used=False,
            ranked_candidates=raw_ranked_candidates,
            baseline_alignment=raw_baseline_alignment,
            override_reason=raw_override_reason,
            urgency_level=urgency_level,
            stage_memory_used=stage_memory_used,
            baseline_top_strategy_id=str(baseline_top.get("strategy_id") or ""),
            baseline_top_utility=_safe_float(baseline_top.get("utility"), 0.0),
            raw_selected_strategy_id=raw_selected_strategy_id,
            raw_baseline_alignment=raw_baseline_alignment,
            raw_override_reason=raw_override_reason,
            raw_reasoning_summary=raw_reasoning_summary,
            raw_ranked_candidates=raw_ranked_candidates,
            executed_via_fallback=True,
            executed_decision_source="system_fallback",
            fallback_constraint_name="",
            decision_mode=decision_mode,
            telemetry_confidence=telemetry_confidence,
            repeat_previous_action=repeat_previous_action,
            why_not_observe=why_not_observe,
            why_not_rate_limit=why_not_rate_limit,
            why_not_quarantine=why_not_quarantine,
        )

    if not selected_strategy:
        fallback_selection = _selection_from_strategy(
            next(
                (item for item in active_defenders if str(item.get("id") or "") == str(baseline_top.get("strategy_id") or "")),
                active_defenders[0] if active_defenders else {},
            ),
            game_result,
            mode="llm_defender_recovered",
            confidence=confidence,
            reasoning_summary="Recovered to the canonical baseline defender because the LLM returned an invalid strategy id.",
            expected_security_gain=expected_security_gain,
            expected_qos_impact=expected_qos_impact,
            expected_controller_cost=expected_controller_cost,
            baseline_alignment=raw_baseline_alignment,
            override_reason=raw_override_reason,
            urgency_level=urgency_level,
            stage_memory_used=stage_memory_used,
            decision_mode=decision_mode,
            telemetry_confidence=telemetry_confidence,
            repeat_previous_action=repeat_previous_action,
            why_not_observe=why_not_observe,
            why_not_rate_limit=why_not_rate_limit,
            why_not_quarantine=why_not_quarantine,
        )
        return DefenderSelectorResult(
            selection=fallback_selection,
            trace=trace,
            raw_response=raw_payload,
            fallback_used=True,
            fallback_reason=f"invalid_defender_strategy_id:{raw_selected_strategy_id or 'missing'}",
            request_success=True,
            request_error=request_error,
            parse_success=parse_success,
            recovery_used=True,
            ranked_candidates=raw_ranked_candidates,
            baseline_alignment=raw_baseline_alignment,
            override_reason=raw_override_reason,
            urgency_level=urgency_level,
            stage_memory_used=stage_memory_used,
            baseline_top_strategy_id=str(baseline_top.get("strategy_id") or ""),
            baseline_top_utility=_safe_float(baseline_top.get("utility"), 0.0),
            raw_selected_strategy_id=raw_selected_strategy_id,
            raw_baseline_alignment=raw_baseline_alignment,
            raw_override_reason=raw_override_reason,
            raw_reasoning_summary=raw_reasoning_summary,
            raw_ranked_candidates=raw_ranked_candidates,
            executed_via_fallback=True,
            executed_decision_source="system_fallback",
            fallback_constraint_name="",
            decision_mode=decision_mode,
            telemetry_confidence=telemetry_confidence,
            repeat_previous_action=repeat_previous_action,
            why_not_observe=why_not_observe,
            why_not_rate_limit=why_not_rate_limit,
            why_not_quarantine=why_not_quarantine,
        )

    high_urgency = bool(live_state.get("attack_active")) and _safe_int(live_state.get("path_stage")) >= 2
    if str(selected_strategy.get("action") or "") == "observe" and high_urgency:
        fallback_strategy = _choose_fallback_strategy(active_defenders, live_state)
        fallback_id = str(fallback_strategy.get("id") or "")
        fallback_selection = _selection_from_strategy(
            fallback_strategy,
            game_result,
            mode="llm_defender_fallback",
            confidence=confidence,
            reasoning_summary=(
                f"The raw LLM selection {raw_selected_strategy_id or 'D0_observe'} was disallowed because observe is "
                f"not allowed during an active progressed attack (path_stage >= 2). The system therefore executed "
                f"{fallback_id} as the highest-priority containment-capable safe fallback."
            ),
            expected_security_gain=expected_security_gain,
            expected_qos_impact=expected_qos_impact,
            expected_controller_cost=expected_controller_cost,
            baseline_alignment=raw_baseline_alignment,
            override_reason=raw_override_reason,
            urgency_level=urgency_level or "critical",
            stage_memory_used=stage_memory_used,
            decision_mode=decision_mode,
            telemetry_confidence=telemetry_confidence,
            repeat_previous_action=repeat_previous_action,
            why_not_observe=why_not_observe,
            why_not_rate_limit=why_not_rate_limit,
            why_not_quarantine=why_not_quarantine,
        )
        return DefenderSelectorResult(
            selection=fallback_selection,
            trace=trace,
            raw_response=raw_payload,
            fallback_used=True,
            fallback_reason="observe_disallowed_high_urgency",
            request_success=True,
            request_error=request_error,
            parse_success=parse_success,
            recovery_used=recovery_used,
            ranked_candidates=raw_ranked_candidates,
            baseline_alignment=raw_baseline_alignment,
            override_reason=raw_override_reason,
            urgency_level=urgency_level or "critical",
            stage_memory_used=stage_memory_used,
            baseline_top_strategy_id=str(baseline_top.get("strategy_id") or ""),
            baseline_top_utility=_safe_float(baseline_top.get("utility"), 0.0),
            raw_selected_strategy_id=raw_selected_strategy_id,
            raw_baseline_alignment=raw_baseline_alignment,
            raw_override_reason=raw_override_reason,
            raw_reasoning_summary=raw_reasoning_summary,
            raw_ranked_candidates=raw_ranked_candidates,
            executed_via_fallback=True,
            executed_decision_source="constraint_fallback",
            fallback_constraint_name="observe_disallowed_high_urgency",
            decision_mode=decision_mode,
            telemetry_confidence=telemetry_confidence,
            repeat_previous_action=repeat_previous_action,
            why_not_observe=why_not_observe,
            why_not_rate_limit=why_not_rate_limit,
            why_not_quarantine=why_not_quarantine,
        )

    selection = _selection_from_strategy(
        selected_strategy,
        game_result,
        mode="llm_defender_recovered" if recovery_used else "llm_defender",
        confidence=confidence,
        reasoning_summary=raw_reasoning_summary,
        expected_security_gain=expected_security_gain,
        expected_qos_impact=expected_qos_impact,
        expected_controller_cost=expected_controller_cost,
        baseline_alignment=raw_baseline_alignment,
        override_reason=raw_override_reason,
        urgency_level=urgency_level,
        stage_memory_used=stage_memory_used,
        decision_mode=decision_mode,
        telemetry_confidence=telemetry_confidence,
        repeat_previous_action=repeat_previous_action,
        why_not_observe=why_not_observe,
        why_not_rate_limit=why_not_rate_limit,
        why_not_quarantine=why_not_quarantine,
    )
    return DefenderSelectorResult(
        selection=selection,
        trace=trace,
        raw_response=raw_payload,
        fallback_used=recovery_used,
        fallback_reason="malformed_response_json:JSONDecodeError" if recovery_used else "",
        request_success=True,
        request_error=request_error,
        parse_success=parse_success,
        recovery_used=recovery_used,
        ranked_candidates=raw_ranked_candidates,
        baseline_alignment=raw_baseline_alignment,
        override_reason=raw_override_reason,
        urgency_level=urgency_level,
        stage_memory_used=stage_memory_used,
        baseline_top_strategy_id=str(baseline_top.get("strategy_id") or ""),
        baseline_top_utility=_safe_float(baseline_top.get("utility"), 0.0),
        raw_selected_strategy_id=raw_selected_strategy_id,
        raw_baseline_alignment=raw_baseline_alignment,
        raw_override_reason=raw_override_reason,
        raw_reasoning_summary=raw_reasoning_summary,
        raw_ranked_candidates=raw_ranked_candidates,
        executed_via_fallback=False,
        executed_decision_source="llm_defender",
        fallback_constraint_name="",
        decision_mode=decision_mode,
        telemetry_confidence=telemetry_confidence,
        repeat_previous_action=repeat_previous_action,
        why_not_observe=why_not_observe,
        why_not_rate_limit=why_not_rate_limit,
        why_not_quarantine=why_not_quarantine,
    )
