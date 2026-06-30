from game.strategy_manager import StrategyManager


def _manager():
    return StrategyManager({
        "attacker_strategies": [{
            "id": "A1_sensor_probe_sen4",
            "scenario_id": "sen4_edge2_clouddb",
            "path": ["sen4", "edge2_gw"],
            "activation": {"min_path_stage": 0, "max_path_stage": 1},
        }],
        "defender_strategies": [{
            "id": "D0_observe", "scenario_id": "*", "action": "observe", "activation": {}
        }],
        "game_parameters": {},
    })


def _state():
    return {
        "scenario_id": "sen4_edge2_clouddb",
        "path_stage": 0,
        "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        "mulval": {
            "plausible_paths": [["sen4", "edge2_gw", "cloud_db"]],
            "current_path_risk": 0.5,
            "mulval_exact_match_found": False,
            "mulval_path_mismatch": True,
        },
        "operational_constraints": {},
    }


def test_strict_blocks_missing_exact_mulval_path():
    active = _manager().active_lists(_state(), strict_preconditions=True)
    assert active["attacker_ids"] == []
    assert active["filtered_out_reasons"]["attackers"]["A1_sensor_probe_sen4"] == (
        "missing_exact_mulval_path_strict"
    )
