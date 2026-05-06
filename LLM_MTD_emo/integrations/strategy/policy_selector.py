#!/usr/bin/env python3
"""Select executable strategies from evolved population shares."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from strategy_manager import strategy_id


def select_strategy(
    strategies: List[Dict[str, Any]],
    population: Dict[str, float],
    utilities: Optional[Dict[str, float]] = None,
    mode: str = "dominant",
    random_seed: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not strategies:
        return None

    utilities = utilities or {}
    if random_seed is not None:
        random.seed(random_seed)

    by_id = {strategy_id(strategy): strategy for strategy in strategies}
    ids = [strategy_id(strategy) for strategy in strategies]

    if mode == "sample":
        total = sum(max(float(population.get(sid, 0.0)), 0.0) for sid in ids)
        if total <= 0:
            chosen_id = random.choice(ids)
        else:
            threshold = random.random() * total
            running = 0.0
            chosen_id = ids[-1]
            for sid in ids:
                running += max(float(population.get(sid, 0.0)), 0.0)
                if running >= threshold:
                    chosen_id = sid
                    break
    else:
        chosen_id = sorted(
            ids,
            key=lambda sid: (
                float(population.get(sid, 0.0)),
                float(utilities.get(sid, 0.0)),
                sid,
            ),
            reverse=True,
        )[0]

    return {
        "id": chosen_id,
        "probability": float(population.get(chosen_id, 0.0)),
        "utility": float(utilities.get(chosen_id, 0.0)),
        "mode": mode,
        "strategy": by_id[chosen_id],
    }


def select_pair(
    attackers: List[Dict[str, Any]],
    defenders: List[Dict[str, Any]],
    game_result: Dict[str, Any],
    mode: str = "dominant",
    random_seed: Optional[int] = None,
) -> Dict[str, Any]:
    attacker = select_strategy(
        attackers,
        game_result.get("attacker_population", {}),
        game_result.get("attacker_utilities", {}),
        mode=mode,
        random_seed=random_seed,
    )
    defender = select_strategy(
        defenders,
        game_result.get("defender_population", {}),
        game_result.get("defender_utilities", {}),
        mode=mode,
        random_seed=None if random_seed is None else random_seed + 1,
    )
    return {
        "attacker": attacker,
        "defender": defender,
    }


def compact_selection(selection: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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
        "live_attack_type": strategy.get("live_attack_type"),
        "expected_effects": strategy.get("expected_effects", []),
        "executor": strategy.get("executor"),
        "action_payload": strategy.get("action_payload"),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select strategies from a game update.")
    parser.add_argument("--active-json", type=Path, required=True)
    parser.add_argument("--game-json", type=Path, required=True)
    parser.add_argument("--mode", default="dominant", choices=["dominant", "sample"])
    parser.add_argument("--random-seed", type=int)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    with args.active_json.open("r", encoding="utf-8") as handle:
        active = json.load(handle)
    with args.game_json.open("r", encoding="utf-8") as handle:
        game = json.load(handle)
    selection = select_pair(
        active.get("attackers", []),
        active.get("defenders", []),
        game,
        mode=args.mode,
        random_seed=args.random_seed,
    )
    json.dump(
        {
            "attacker": compact_selection(selection.get("attacker")),
            "defender": compact_selection(selection.get("defender")),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
