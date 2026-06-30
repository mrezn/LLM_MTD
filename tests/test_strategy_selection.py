from __future__ import annotations

import json

from game.policy_selector import select_strategy
from game.strategy_manager import StrategyManager


def test_best_utility_mode_selects_highest_utility():
    strategies = [{"id": "D0"}, {"id": "D1"}]
    selected = select_strategy(
        strategies,
        population={"D0": 0.9, "D1": 0.1},
        utilities={"D0": 0.2, "D1": 0.8},
        mode="best_utility",
    )
    assert selected["id"] == "D1"


def test_filtered_out_reasons_are_recorded():
    manager = StrategyManager({
        "attacker_strategies": [{"id": "A1", "scenario_id": "other", "activation": {}}],
        "defender_strategies": [{"id": "D0", "scenario_id": "*", "action": "observe", "activation": {}}],
        "game_parameters": {},
    })
    active = manager.active_lists({"scenario_id": "here", "path_stage": 0, "operational_constraints": {}}, strict_preconditions=False)
    assert active["filtered_out_reasons"]["attackers"]["A1"] == "scenario_mismatch"

