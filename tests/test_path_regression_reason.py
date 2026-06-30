from game.state_builder import derive_effective_path_stage


def test_blocking_at_zero_is_not_reported_as_regression():
    assert derive_effective_path_stage(0, True, False, False, False) == (
        0, "blocking_active_no_regression"
    )


def test_blocking_that_reduces_stage_reports_reason():
    assert derive_effective_path_stage(3, True, False, False, False) == (
        2, "drop_rules_active"
    )
