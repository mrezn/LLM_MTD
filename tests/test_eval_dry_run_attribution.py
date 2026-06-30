from __future__ import annotations

from types import SimpleNamespace

from eval.runners.run_stage import build_stage_outcome


def _defender_result():
    selection = {"id": "D1", "action": "quarantine_sensor", "target": "sen4", "expected_effects": ["drop_rules_active"], "reasoning_summary": "x"}
    return SimpleNamespace(
        selection=selection,
        request_success=True,
        parse_success=True,
        fallback_used=False,
        recovery_used=False,
        request_error="",
    )


def test_dry_run_does_not_inherit_live_defense_effects():
    state = {"path_stage": 1, "attack_active": True}
    next_state = {
        "path_stage": 1,
        "attack_active": True,
        "drop_rules_active": True,
        "counters_stopped": False,
        "defense_active": True,
        "overhead": {"controller_active_actions": 1, "flow_rules_installed": 20, "meters_added": 0},
    }
    outcome = build_stage_outcome(
        state=state,
        next_state=next_state,
        attacker_execution={"status": "dispatched"},
        defender_execution={"status": "dry_run", "payload": {"action": "quarantine_sensor", "target": "sen4"}},
        defender_result=_defender_result(),
    )
    validation = outcome["stage_validation"]
    summary = outcome["state_summary"]
    assert validation["defense_confirmed"] is False
    assert validation["defense_effects_confirmed"] is False
    assert validation["attribution_source"] == "dry_run_no_execution"
    assert summary["drop_rules_active"] is False
    assert summary["live_environment_has_active_defenses"] is True

