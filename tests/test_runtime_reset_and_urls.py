from __future__ import annotations

from game.strategy_runtime import local_container_url, stage_teardown


def test_local_container_url_preserves_host_and_query():
    assert (
        local_container_url("http://cloud_logger:8000/path?x=1", "/attack/event")
        == "http://cloud_logger:8000/path?x=1"
    )


def test_stage_teardown_clear_all_idempotent(monkeypatch):
    calls = {"delete": 0}

    def fake_request_json(url, method="GET", timeout=2.0):
        if method == "GET":
            count = 1 if calls["delete"] == 0 else 0
            return {"ok": True, "json": {"active_actions": [{}] * count}}
        calls["delete"] += 1
        return {"ok": True, "status": 202, "json": {}}

    monkeypatch.setattr("game.strategy_runtime.request_json", fake_request_json)
    result = stage_teardown("http://127.0.0.1:8080/mtd/action", "http://127.0.0.1:8080/mtd/status", 2.0, reset_actions=True)
    assert result["pre_stage_reset_success"] is True
    assert result["pre_stage_active_policy_count_before"] == 1
    assert result["pre_stage_active_policy_count_after"] == 0

