#!/usr/bin/env python3
"""Maintain the full and active strategy libraries for a strategy stage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


STRATEGY_DIR = Path(__file__).resolve().parent
DEFAULT_STRATEGY_SPACE = STRATEGY_DIR / "strategy_space.json"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ok", "success")
    return False


def get_state_value(state: Dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = state
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def strategy_id(strategy: Dict[str, Any]) -> str:
    return str(strategy.get("id", ""))


def load_strategy_space(path: Path = DEFAULT_STRATEGY_SPACE) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    validate_strategy_space(data, path)
    return data


def validate_strategy_space(data: Dict[str, Any], path: Path) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"strategy space must be a JSON object: {path}")
    for key in ("attacker_strategies", "defender_strategies"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"{key} must be a list in {path}")
        seen = set()
        for strategy in data[key]:
            sid = strategy_id(strategy)
            if not sid:
                raise ValueError(f"strategy without id in {key}")
            if sid in seen:
                raise ValueError(f"duplicate strategy id {sid} in {key}")
            seen.add(sid)


class StrategyManager:
    """Filter the full strategy space into A_active(t) and D_active(t)."""

    def __init__(self, strategy_space: Dict[str, Any]):
        self.strategy_space = strategy_space
        self.attacker_strategies = list(strategy_space.get("attacker_strategies", []))
        self.defender_strategies = list(strategy_space.get("defender_strategies", []))
        self.parameters = dict(strategy_space.get("game_parameters", {}))

    @classmethod
    def from_file(cls, path: Path = DEFAULT_STRATEGY_SPACE) -> "StrategyManager":
        return cls(load_strategy_space(path))

    def active_attackers(
        self,
        state: Dict[str, Any],
        strict_preconditions: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        strict = self._strict_preconditions(state, strict_preconditions)
        return [
            strategy for strategy in self.attacker_strategies
            if self._is_active(strategy, state, role="attacker", strict_preconditions=strict)
        ]

    def active_defenders(
        self,
        state: Dict[str, Any],
        strict_preconditions: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        strict = self._strict_preconditions(state, strict_preconditions)
        active = [
            strategy for strategy in self.defender_strategies
            if self._is_active(strategy, state, role="defender", strict_preconditions=strict)
        ]

        if not active:
            active = [
                strategy for strategy in self.defender_strategies
                if strategy.get("action") == "observe"
            ]
        return active

    def active_lists(
        self,
        state: Dict[str, Any],
        strict_preconditions: Optional[bool] = None,
    ) -> Dict[str, Any]:
        attackers = self.active_attackers(state, strict_preconditions=strict_preconditions)
        defenders = self.active_defenders(state, strict_preconditions=strict_preconditions)
        return {
            "scenario_id": state.get("scenario_id"),
            "path_stage": state.get("path_stage"),
            "attackers": attackers,
            "defenders": defenders,
            "attacker_ids": [strategy_id(strategy) for strategy in attackers],
            "defender_ids": [strategy_id(strategy) for strategy in defenders],
        }

    def _strict_preconditions(
        self,
        state: Dict[str, Any],
        strict_preconditions: Optional[bool],
    ) -> bool:
        if strict_preconditions is not None:
            return strict_preconditions
        return bool_value(
            get_state_value(state, "operational_constraints.strict_preconditions", False)
        )

    def _is_active(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
        role: str,
        strict_preconditions: bool,
    ) -> bool:
        if not self._scenario_matches(strategy, state):
            return False
        if role == "attacker" and not self._mulval_allows_attacker(strategy, state):
            return False
        if role == "attacker" and self._attack_path_contained(strategy, state):
            return False
        if not self._activation_matches(strategy, state, strict_preconditions):
            return False
        if not self._budget_allows(strategy, state, role):
            return False
        if role == "defender" and self._defense_already_active(strategy, state):
            return False
        return True

    def _scenario_matches(self, strategy: Dict[str, Any], state: Dict[str, Any]) -> bool:
        strategy_scenario = str(strategy.get("scenario_id", "*"))
        return strategy_scenario in ("*", str(state.get("scenario_id", "")))

    def _activation_matches(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
        strict_preconditions: bool,
    ) -> bool:
        activation = strategy.get("activation") or {}
        path_stage = safe_float(state.get("path_stage"))
        min_stage = safe_float(activation.get("min_path_stage", 0.0))
        max_stage = safe_float(activation.get("max_path_stage", 99.0))
        if path_stage < min_stage or path_stage > max_stage:
            return False

        for flag in activation.get("required_flags", []):
            if not bool_value(get_state_value(state, str(flag), False)):
                return False

        for flag in activation.get("blocked_flags", []):
            if bool_value(get_state_value(state, str(flag), False)):
                return False

        if activation.get("target_in_path"):
            target = strategy.get("target") or (strategy.get("action_payload") or {}).get("target")
            if target and target not in state.get("current_path", []):
                return False

        if activation.get("requires_controller") and strict_preconditions:
            if not bool_value(state.get("controller_reachable")):
                return False

        if activation.get("requires_mulval_path") and not self._strategy_path_is_plausible(
            strategy,
            state,
        ):
            return False

        return True

    def _strategy_path(self, strategy: Dict[str, Any]) -> List[str]:
        path = strategy.get("path")
        if not isinstance(path, list):
            return []
        return [str(item) for item in path]

    def _path_key(self, path: Iterable[Any]) -> str:
        return "->".join(str(item) for item in path)

    def _strategy_path_is_plausible(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
    ) -> bool:
        strategy_path = self._strategy_path(strategy)
        if not strategy_path:
            return True

        plausible_paths = (state.get("mulval") or {}).get("plausible_paths") or []
        if not plausible_paths:
            return True

        strategy_key = self._path_key(strategy_path)
        plausible_keys = {self._path_key(path) for path in plausible_paths}
        if strategy_key in plausible_keys:
            return True

        activation = strategy.get("activation") or {}
        if activation.get("dual_homed_alternative") and strategy.get("entry_node") == "sen6":
            return True

        current_path = state.get("current_path") or []
        return strategy_key == self._path_key(current_path)

    def _mulval_allows_attacker(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
    ) -> bool:
        if not self._strategy_path_is_plausible(strategy, state):
            return False

        risk = safe_float((state.get("mulval") or {}).get("current_path_risk"), 0.0)
        min_risk = safe_float((strategy.get("activation") or {}).get("min_mulval_risk"), 0.0)
        return risk >= min_risk

    def _attack_path_contained(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
    ) -> bool:
        if not (
            bool_value(state.get("defense_success"))
            and (
                bool_value(state.get("defense_active"))
                or bool_value(state.get("drop_rules_active"))
                or bool_value(state.get("counters_stopped"))
            )
        ):
            return False
        activation = strategy.get("activation") or {}
        if activation.get("dual_homed_alternative"):
            return False
        return True

    def _defense_already_active(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
    ) -> bool:
        action = strategy.get("action") or (strategy.get("action_payload") or {}).get("action")
        if action == "observe":
            return False

        target = strategy.get("target") or (strategy.get("action_payload") or {}).get("target")
        if not action or not target:
            return False

        for active_action in (state.get("controller") or {}).get("active_actions", []):
            if not isinstance(active_action, dict):
                continue
            if active_action.get("action") == action and active_action.get("target") == target:
                return True
            if active_action.get("target") == target and bool_value(state.get("drop_rules_active")):
                return True
        return False

    def _budget_allows(
        self,
        strategy: Dict[str, Any],
        state: Dict[str, Any],
        role: str,
    ) -> bool:
        constraints = state.get("operational_constraints") or {}
        base_cost = safe_float(strategy.get("base_cost"))
        if role == "attacker":
            return base_cost <= safe_float(constraints.get("max_attack_cost", 1.0), 1.0)

        if base_cost > safe_float(constraints.get("max_defense_cost", 1.0), 1.0):
            return False
        if not bool_value(constraints.get("allow_disruptive_defense", True)):
            if base_cost >= 0.40:
                return False
        return True


def summarize_strategies(strategies: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": strategy.get("id"),
            "name": strategy.get("name"),
            "scenario_id": strategy.get("scenario_id"),
            "action": strategy.get("action") or strategy.get("live_attack_type"),
            "target": strategy.get("target") or strategy.get("target_asset"),
            "base_cost": strategy.get("base_cost"),
            "base_reward": strategy.get("base_reward"),
        }
        for strategy in strategies
    ]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List active LLM_MTD_emo strategies.")
    parser.add_argument("--strategy-space", type=Path, default=DEFAULT_STRATEGY_SPACE)
    parser.add_argument("--state-json", type=Path, required=True)
    parser.add_argument("--strict-preconditions", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    manager = StrategyManager.from_file(args.strategy_space)
    with args.state_json.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    active = manager.active_lists(state, strict_preconditions=args.strict_preconditions)
    payload = {
        "scenario_id": active["scenario_id"],
        "path_stage": active["path_stage"],
        "active_attackers": summarize_strategies(active["attackers"]),
        "active_defenders": summarize_strategies(active["defenders"]),
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
