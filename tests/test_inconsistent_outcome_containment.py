from types import SimpleNamespace

from eval.runners.run_stage import build_stage_outcome


def test_attack_effect_and_confirmed_regression_are_consistent():
    defender = SimpleNamespace(
        selection={"id": "D1", "expected_effects": ["drop_rules_active"]},
        request_success=True, parse_success=True, fallback_used=False,
    )
    outcome = build_stage_outcome(
        state={"attack_active": True, "attack_effect_success": True, "path_stage": 3, "current_path": ["sen4"]},
        next_state={"attack_active": True, "attack_effect_success": True, "path_stage": 2, "drop_rules_active": True, "overhead": {"controller_active_actions": 1, "flow_rules_installed": 4}},
        attacker_execution={"status": "dispatched"},
        defender_execution={"status": "executed", "payload": {"action": "quarantine_sensor", "target": "sen4"}, "post_result": {"ok": True, "body": '{"status":"installed","flow_rules_installed":4,"active_policy_actions":1}'}},
        defender_result=defender,
    )
    assert "inconsistent_outcome" not in outcome["stage_validation"]["invalidity_reasons"]
    assert outcome["stage_validation"]["path_regressed"] is True
