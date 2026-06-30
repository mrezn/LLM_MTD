from types import SimpleNamespace

from eval.runners.run_stage import build_stage_outcome


def test_dry_run_is_never_paper_valid():
    defender = SimpleNamespace(
        selection={"id": "D0", "expected_effects": []},
        request_success=True, parse_success=True, fallback_used=False,
    )
    result = build_stage_outcome(
        state={"attack_active": True, "path_stage": 1},
        next_state={"attack_active": True, "path_stage": 1},
        attacker_execution={"status": "dispatched"},
        defender_execution={"status": "dry_run", "payload": {"action": "observe"}},
        defender_result=defender,
    )
    validation = result["stage_validation"]
    assert validation["stage_kind"] == "dry_run"
    assert validation["paper_valid_stage"] is False
    assert "defender_dry_run" in validation["invalidity_reasons"]
