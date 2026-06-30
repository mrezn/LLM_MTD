#!/usr/bin/env python3
"""Evolutionary attacker-defender game over active strategy lists."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .strategy_manager import strategy_id
except ImportError:
    from strategy_manager import strategy_id  # script mode fallback


DEFAULT_PARAMETERS = {
    "eta_attacker": 0.18,
    "eta_defender": 0.22,
    "omega_ac": 0.65,
    "omega_gp": 0.25,
    "omega_dc": 0.40,
    "omega_r": 0.25,
    "regulatory_beta": 0.18,
    "population_floor": 0.01,
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def numeric_components(strategy: Dict[str, Any], key: str) -> Dict[str, float]:
    raw = strategy.get(key) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): safe_float(value) for name, value in raw.items()}


def component_mean(strategy: Dict[str, Any], key: str) -> float:
    return mean(numeric_components(strategy, key).values())


def merge_parameters(parameters: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    merged = DEFAULT_PARAMETERS.copy()
    if parameters:
        for key, value in parameters.items():
            if key in merged:
                merged[key] = safe_float(value, merged[key])
    return merged


def path_progress(state: Dict[str, Any]) -> float:
    return clamp(safe_float(state.get("path_stage")) / 3.0)


def attacker_theta(state: Dict[str, Any]) -> float:
    return clamp(
        safe_float(
            (state.get("attacker_observation") or {}).get("estimated_success_probability"),
            default=0.10 + 0.25 * safe_float(state.get("path_stage")),
        )
    )


def target_match(attacker: Dict[str, Any], defender: Dict[str, Any], state: Dict[str, Any]) -> float:
    defender_target = defender.get("target") or (defender.get("action_payload") or {}).get("target")
    if not defender_target:
        return 0.10 if defender.get("action") == "observe" else 0.0

    attack_path = attacker.get("path") or state.get("current_path", [])
    entry_node = attacker.get("entry_node") or state.get("entry_node")
    target_asset = attacker.get("target_asset") or state.get("target_asset")

    score = 0.0
    if defender_target == entry_node:
        score += 0.35
    if defender_target == target_asset:
        score += 0.20
    if defender_target in attack_path:
        score += 0.35
    if defender.get("scenario_id") in ("*", attacker.get("scenario_id")):
        score += 0.10
    return clamp(score)


def action_effectiveness(defender: Dict[str, Any], attacker: Dict[str, Any], state: Dict[str, Any]) -> float:
    action = str(defender.get("action") or (defender.get("action_payload") or {}).get("action") or "")
    base = safe_float(defender.get("base_reward"), 0.0)
    match = target_match(attacker, defender, state)
    action_bonus = {
        "observe": 0.05,
        "rate_limit": 0.42,
        "rate_limit_sensor": 0.42,
        "reroute_traffic": 0.62,
        "reroute_sensor": 0.62,
        "quarantine_sensor": 0.78,
        "isolate_sensor": 0.74,
    }.get(action, 0.25)

    stage_bonus = 0.08 if safe_float(state.get("path_stage")) >= 1 else 0.0
    return clamp((0.45 * base) + (0.35 * action_bonus) + (0.20 * match) + stage_bonus)


def sal_proxy(attacker: Dict[str, Any], defender: Dict[str, Any], state: Dict[str, Any]) -> float:
    """Security Attribute Loss proxy."""
    base_reward = safe_float(attacker.get("base_reward"), 0.5)
    mulval_risk = safe_float((state.get("mulval") or {}).get("current_path_risk"), 0.0)
    criticality = max(safe_float(attacker.get("target_criticality"), base_reward), mulval_risk)
    theta = max(attacker_theta(state), path_progress(state))
    path = attacker.get("path") or state.get("current_path", [])
    lateral_factor = 1.0 + (0.04 * max(len(path) - 2, 0))
    impact_bonus = 0.12 if state.get("attack_effect_success") else 0.0
    defense_reduction = 0.62 * action_effectiveness(defender, attacker, state)
    raw = ((0.65 * base_reward) + (0.35 * criticality)) * (0.30 + 0.70 * theta)
    return clamp((raw * lateral_factor + impact_bonus) * (1.0 - defense_reduction))


def ac_proxy(attacker: Dict[str, Any], _state: Dict[str, Any]) -> float:
    """Attack Cost proxy."""
    base_cost = safe_float(attacker.get("base_cost"), 0.0)
    component_cost = component_mean(attacker, "attack_cost")
    return clamp((0.55 * base_cost) + (0.45 * component_cost))


def gp_proxy(attacker: Dict[str, Any], state: Dict[str, Any], parameters: Dict[str, float]) -> float:
    """Regulatory / detection penalty proxy G_p = beta * theta."""
    beta = safe_float(attacker.get("regulatory_beta"), parameters["regulatory_beta"])
    risk = safe_float((state.get("attacker_observation") or {}).get("estimated_risk"), 0.0)
    return clamp(beta * attacker_theta(state) * (1.0 + risk))


def sap_proxy(attacker: Dict[str, Any], defender: Dict[str, Any], state: Dict[str, Any]) -> float:
    """Security Attribute Protection proxy."""
    mission_value = safe_float(attacker.get("target_criticality"), attacker.get("base_reward", 0.5))
    attack_pressure = clamp(
        0.20
        + (0.55 * path_progress(state))
        + (0.15 if state.get("attack_effect_success") else 0.0)
        + (0.10 if state.get("attack_active") else 0.0)
    )
    effectiveness = action_effectiveness(defender, attacker, state)
    observed_success = (
        0.12 if state.get("drop_rules_active") else 0.0
    ) + (
        0.18 if state.get("counters_stopped") else 0.0
    ) + (
        0.12 if state.get("defense_success") else 0.0
    )
    return clamp(mission_value * attack_pressure * (0.45 + 0.55 * effectiveness) + observed_success)


def dc_proxy(defender: Dict[str, Any], state: Dict[str, Any]) -> float:
    """Defense Cost proxy."""
    base_cost = safe_float(defender.get("base_cost"), 0.0)
    component_cost = component_mean(defender, "defense_cost")
    overhead = state.get("overhead") or {}
    qos = state.get("qos") or {}
    controller_cost = clamp(safe_float(overhead.get("controller_apply_ms")) / 100.0)
    flow_rule_cost = max(
        safe_float(overhead.get("flow_rules_installed_delta")),
        safe_float(overhead.get("active_mtd_flow_count")),
    )
    rule_cost = clamp(
        (
            flow_rule_cost
            + safe_float(overhead.get("flow_delete_commands"))
            + safe_float(overhead.get("meters_added_delta", 0))
        )
        / 120.0
    )
    service_cost = clamp(
        (safe_float(qos.get("sensor_to_edge_latency_ms")) + safe_float(qos.get("edge_to_cloud_latency_ms")))
        / 500.0
        + safe_float(qos.get("loss_rate"))
    )
    raw_dc = (
        (0.50 * base_cost)
        + (0.25 * component_cost)
        + (0.15 * controller_cost)
        + (0.05 * rule_cost)
        + (0.05 * service_cost)
    )
    urgency = path_progress(state)
    return clamp(raw_dc * (1.0 - 0.5 * urgency))


def r_alpha_proxy(defender: Dict[str, Any], state: Dict[str, Any]) -> float:
    """Incentive reward proxy."""
    base = safe_float(defender.get("incentive_reward"), 0.0)
    context_reward = 0.03 if state.get("defense_active") or state.get("attack_active") else 0.0
    return clamp(base + context_reward)


def attacker_utility(
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    state: Dict[str, Any],
    parameters: Optional[Dict[str, Any]] = None,
) -> float:
    params = merge_parameters(parameters)
    sal = sal_proxy(attacker, defender, state)
    ac = ac_proxy(attacker, state)
    gp = gp_proxy(attacker, state, params)
    return sal - (params["omega_ac"] * ac) - (params["omega_gp"] * gp)


def defender_utility(
    attacker: Dict[str, Any],
    defender: Dict[str, Any],
    state: Dict[str, Any],
    parameters: Optional[Dict[str, Any]] = None,
) -> float:
    params = merge_parameters(parameters)
    sap = sap_proxy(attacker, defender, state)
    dc = dc_proxy(defender, state)
    reward = r_alpha_proxy(defender, state)
    return sap + (params["omega_r"] * reward) - (params["omega_dc"] * dc)


def normalize_population(
    ids: List[str],
    previous: Optional[Dict[str, float]] = None,
    epsilon: float = 0.01,
) -> Dict[str, float]:
    if not ids:
        return {}
    previous = previous or {}
    uniform = 1.0 / len(ids)
    values = {sid: max(safe_float(previous.get(sid), uniform), 0.0) for sid in ids}
    return apply_population_floor(values, epsilon)


def apply_population_floor(values: Dict[str, float], epsilon: float = 0.01) -> Dict[str, float]:
    if not values:
        return {}
    count = len(values)
    floor = min(max(float(epsilon), 0.0), 1.0 / count)
    residual = max(1.0 - (floor * count), 0.0)
    positive = {key: max(safe_float(value), 0.0) for key, value in values.items()}
    total = sum(positive.values())
    weights = (
        {key: value / total for key, value in positive.items()}
        if total > 0
        else {key: 1.0 / count for key in positive}
    )
    return {key: floor + residual * weights[key] for key in positive}


def replicator_update(
    population: Dict[str, float],
    utilities: Dict[str, float],
    eta: float,
    epsilon: float = 0.01,
) -> Tuple[Dict[str, float], float]:
    if not population:
        return {}, 0.0
    average = sum(population[sid] * utilities.get(sid, 0.0) for sid in population)
    updated = {}
    for sid, share in population.items():
        next_share = share + eta * share * (utilities.get(sid, 0.0) - average)
        updated[sid] = max(next_share, 0.0)
    return apply_population_floor(updated, epsilon), average


def expected_utilities(
    attackers: List[Dict[str, Any]],
    defenders: List[Dict[str, Any]],
    attacker_population: Dict[str, float],
    defender_population: Dict[str, float],
    state: Dict[str, Any],
    parameters: Dict[str, float],
) -> Dict[str, Any]:
    attacker_payoffs: Dict[str, Dict[str, float]] = {}
    defender_payoffs: Dict[str, Dict[str, float]] = {}

    for attacker in attackers:
        aid = strategy_id(attacker)
        attacker_payoffs[aid] = {}
        defender_payoffs[aid] = {}
        for defender in defenders:
            did = strategy_id(defender)
            attacker_payoffs[aid][did] = attacker_utility(attacker, defender, state, parameters)
            defender_payoffs[aid][did] = defender_utility(attacker, defender, state, parameters)

    attacker_utilities = {
        strategy_id(attacker): sum(
            defender_population.get(strategy_id(defender), 0.0)
            * attacker_payoffs[strategy_id(attacker)].get(strategy_id(defender), 0.0)
            for defender in defenders
        )
        for attacker in attackers
    }
    defender_utilities = {
        strategy_id(defender): sum(
            attacker_population.get(strategy_id(attacker), 0.0)
            * defender_payoffs[strategy_id(attacker)].get(strategy_id(defender), 0.0)
            for attacker in attackers
        )
        for defender in defenders
    }

    return {
        "attacker_payoffs": attacker_payoffs,
        "defender_payoffs": defender_payoffs,
        "attacker_utilities": attacker_utilities,
        "defender_utilities": defender_utilities,
    }


def evolutionary_step(
    attackers: List[Dict[str, Any]],
    defenders: List[Dict[str, Any]],
    state: Dict[str, Any],
    previous_population: Optional[Dict[str, Dict[str, float]]] = None,
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    params = merge_parameters(parameters)
    attacker_ids = [strategy_id(strategy) for strategy in attackers]
    defender_ids = [strategy_id(strategy) for strategy in defenders]
    previous_population = previous_population or {}

    attacker_population = normalize_population(
        attacker_ids,
        previous_population.get("attacker"),
        params["population_floor"],
    )
    defender_population = normalize_population(
        defender_ids,
        previous_population.get("defender"),
        params["population_floor"],
    )

    utilities = expected_utilities(
        attackers,
        defenders,
        attacker_population,
        defender_population,
        state,
        params,
    )
    updated_attacker_population, avg_attacker = replicator_update(
        attacker_population,
        utilities["attacker_utilities"],
        params["eta_attacker"],
        params["population_floor"],
    )
    updated_defender_population, avg_defender = replicator_update(
        defender_population,
        utilities["defender_utilities"],
        params["eta_defender"],
        params["population_floor"],
    )

    return {
        "parameters": params,
        "attacker_population_before": attacker_population,
        "defender_population_before": defender_population,
        "attacker_population": updated_attacker_population,
        "defender_population": updated_defender_population,
        "attacker_average_utility": avg_attacker,
        "defender_average_utility": avg_defender,
        **utilities,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one evolutionary strategy update.")
    parser.add_argument("--state-json", type=Path, required=True)
    parser.add_argument("--active-json", type=Path, required=True)
    parser.add_argument("--population-json", type=Path)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    with args.state_json.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    with args.active_json.open("r", encoding="utf-8") as handle:
        active = json.load(handle)
    previous = {}
    if args.population_json and args.population_json.exists():
        with args.population_json.open("r", encoding="utf-8") as handle:
            previous = json.load(handle)
    result = evolutionary_step(
        active.get("attackers", []),
        active.get("defenders", []),
        state,
        previous_population=previous,
    )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
