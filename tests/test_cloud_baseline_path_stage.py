from game.state_builder import derive_cloud_seen


def test_equal_cloud_baseline_does_not_create_cloud_evidence():
    result = derive_cloud_seen(
        attack_metrics={}, cloud_storage_current=100,
        constraints={"cloud_storage_baseline": 100, "attacker_execution": {"abilities_ran": 1}},
        has_attack_context=True,
    )
    assert result["cloud_seen"] is False
    assert result["cloud_storage_delta"] == 0
