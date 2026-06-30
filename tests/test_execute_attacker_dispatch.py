from game.strategy_runtime import execute_attacker


SELECTION = {
    "id": "A1",
    "strategy": {
        "id": "A1",
        "name": "probe",
        "path": ["sen4", "edge2_gw"],
        "live_attack_type": "sensor_to_edge_probe",
        "executor": {"type": "caldera_adversary", "adversary_yaml_id": "adv-1"},
    },
}


def test_active_attacker_dispatches(monkeypatch):
    monkeypatch.setattr("game.strategy_runtime.post_json", lambda *args, **kwargs: {
        "ok": True, "status": 202, "body": "{}", "url": args[0], "error": ""
    })
    result = execute_attacker(
        SELECTION, {"scenario_id": "s", "entry_node": "sen4"}, True,
        "http://bridge/caldera/dispatch", "", "", 2.0,
    )
    assert result["status"] == "dispatched"
    assert result["dispatch_attempted"] is True
    assert result["post_result"]["status"] == 202
