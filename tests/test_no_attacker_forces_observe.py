from game.strategy_manager import StrategyManager
from game.strategy_runtime import apply_no_attack_fallback


def test_no_attacker_at_clean_stage_forces_observe():
    manager = StrategyManager({
        "attacker_strategies": [],
        "defender_strategies": [
            {"id": "D0_observe", "action": "observe"},
            {"id": "D1_quarantine", "action": "quarantine_sensor"},
        ],
        "game_parameters": {},
    })
    active = {"attacker_ids": [], "attackers": [], "defenders": manager.defender_strategies}
    assert apply_no_attack_fallback(active, manager, {"path_stage": 0}) is True
    assert active["defender_ids"] == ["D0_observe"]
