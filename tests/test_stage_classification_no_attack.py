from game.stage_transition import evidence_summary


def test_no_active_attacker_stage_is_not_paper_or_learning_valid():
    summary = evidence_summary(
        {"path_stage": 0, "raw_path_stage": 0, "effective_path_stage": 0},
        {"attacker": {"status": "no_active_attacker_strategy"}},
        {},
    )
    outcome = summary["outcome"]
    assert outcome["classification"] == "no_active_attacker_strategy"
    assert outcome["paper_valid"] is False
    assert outcome["learning_valid"] is False
    assert outcome["comparable_attack"] is False
