from eval.runners.run_stage import _canonical_stage_summary_text


def test_unconfirmed_execution_narrative_is_not_optimistic():
    text = _canonical_stage_summary_text(
        stage_id=1, scenario_id="s", attacker_strategy_id="A", defender_strategy_id="D",
        defender_execution={"status": "executed", "payload": {"action": "quarantine_sensor"}},
        validation={"comparable_stage": True, "defense_executed": True, "path_stage_after": 2, "path_regressed": False},
        security_outcome={"attack_active": True, "attack_effect_success": True, "defense_confirmed": False, "defense_effects_confirmed": False, "defense_success": False, "defense_applied_but_not_effective": False},
        reasoning_summary="",
    )
    assert "defense executed but not confirmed" in text
