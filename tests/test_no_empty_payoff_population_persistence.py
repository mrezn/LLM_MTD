from game.strategy_runtime import stage_persistence_policy


def test_empty_attacker_set_skips_learning_but_keeps_audit_log():
    policy = stage_persistence_policy(source_invalid=False, no_active_attack=True)
    assert policy["skip_payoff_update"] is True
    assert policy["skip_population_persistence"] is True
    assert policy["skip_audit_logs"] is False
