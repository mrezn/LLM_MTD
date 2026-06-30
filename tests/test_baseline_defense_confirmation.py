from eval.reports.load_results import _flatten_stage_row


def test_baseline_confirmation_is_inferred_from_successful_ryu_install():
    row = {
        "stage_id": 1,
        "state_summary": {"scenario_id": "s", "attack_active": True},
        "selection": {"defender": {"id": "D1", "action": "quarantine_sensor", "expected_effects": ["drop_rules_active"]}},
        "execution": {"defender": {"status": "executed", "post_result": {"ok": True}}},
        "execution_summary": {"defender": {"status": "executed", "ryu_response": {"active_policy_actions": 1, "flow_rules_installed": 4}}},
        "next_state": {"drop_rules_active": True, "overhead": {"controller_active_actions": 1}},
    }
    flattened = _flatten_stage_row(row, method="baseline_game", trace_index={})
    assert flattened["defense_confirmed"] is True
    assert flattened["defense_effects_confirmed"] is True
