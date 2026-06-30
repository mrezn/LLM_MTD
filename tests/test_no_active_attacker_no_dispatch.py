from game.strategy_runtime import execute_attacker


def test_no_active_attacker_has_actionable_no_dispatch_result():
    result = execute_attacker(
        None, {}, True, "http://bridge/caldera/dispatch", "", "", 2.0,
        filtered_out_reasons={"A1": "activation_constraints"},
    )
    assert result["status"] == "no_active_attacker_strategy"
    assert result["dispatch_attempted"] is False
    assert result["post_result"] is None
    assert result["failure_reason"] == "all_attackers_filtered"
    assert result["remediation_hint"]
