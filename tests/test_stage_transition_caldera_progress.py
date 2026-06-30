from __future__ import annotations

from game.stage_transition import infer_transition, selection_summary


def test_attacker_progress_requires_caldera_abilities():
    selection = {
        "attacker": {"id": "A1", "strategy": {"expected_effects": []}},
        "defender": {"id": "D0", "strategy": {"expected_effects": []}},
    }
    execution = {"attacker": {"abilities_ran": 0, "status": "dispatched"}, "defender": {"status": "observe_only"}}
    transition = infer_transition({"path_stage": 0}, {"path_stage": 3}, selection, execution)
    assert transition["attacker_progressed"] is False


def test_attacker_progress_when_stage_increases_and_abilities_ran():
    selection = {
        "attacker": {"id": "A1", "strategy": {"expected_effects": []}},
        "defender": {"id": "D0", "strategy": {"expected_effects": []}},
    }
    execution = {"attacker": {"abilities_ran": 2, "status": "dispatched"}, "defender": {"status": "observe_only"}}
    transition = infer_transition({"path_stage": 1}, {"path_stage": 2}, selection, execution)
    assert transition["attacker_progressed"] is True


def test_best_utility_selection_note():
    summary = selection_summary(
        {"defender": {"id": "D1", "mode": "best_utility", "strategy": {"id": "D1"}}},
        "defender",
        {
            "defender_population_before": {"D1": 0.3},
            "defender_population": {"D1": 0.4},
            "defender_utilities": {"D1": 0.9},
        },
    )
    assert summary["selection_mode"] == "best_utility"
    assert summary["selected_by"] == "utility"
    assert "highest current utility" in summary["selection_note"]
