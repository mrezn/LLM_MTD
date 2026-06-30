from __future__ import annotations

from game.game_model import defender_utility


def _state(path_stage: int, attack_active: bool, attack_effect_success: bool):
    return {
        "path_stage": path_stage,
        "attack_active": attack_active,
        "attack_effect_success": attack_effect_success,
        "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        "entry_node": "sen4",
        "target_asset": "cloud_db",
        "mulval": {"current_path_risk": 0.82},
        "overhead": {},
        "qos": {},
    }


def test_active_defense_outranks_observe_at_high_path_stage():
    attacker = {
        "id": "A6",
        "entry_node": "sen4",
        "path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        "target_asset": "cloud_db",
        "target_criticality": 0.82,
        "base_reward": 0.82,
        "base_cost": 0.45,
    }
    observe = {"id": "D0", "action": "observe", "base_reward": 0.10, "base_cost": 0.0}
    quarantine = {"id": "D1", "action": "quarantine_sensor", "target": "sen4", "base_reward": 0.82, "base_cost": 0.32}
    state = _state(path_stage=2, attack_active=True, attack_effect_success=True)
    assert defender_utility(attacker, quarantine, state) > defender_utility(attacker, observe, state)


def test_attacker_differences_change_defender_payoff():
    defender = {"id": "D1", "action": "quarantine_sensor", "target": "sen4", "base_reward": 0.82, "base_cost": 0.32}
    recon = {
        "id": "A1",
        "entry_node": "sen4",
        "path": ["sen4", "edge2_gw"],
        "target_asset": "edge2_gw",
        "target_criticality": 0.30,
        "base_reward": 0.30,
        "base_cost": 0.10,
    }
    cloud = {
        "id": "A6",
        "entry_node": "sen4",
        "path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
        "target_asset": "cloud_db",
        "target_criticality": 0.82,
        "base_reward": 0.82,
        "base_cost": 0.45,
    }
    state = _state(path_stage=2, attack_active=True, attack_effect_success=True)
    assert defender_utility(recon, defender, state) != defender_utility(cloud, defender, state)
