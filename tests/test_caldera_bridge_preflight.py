from __future__ import annotations

from attacker.engine.caldera_dispatch_bridge import summarize_chain, verify_adversary_exists


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def request(self, method, path, payload=None, form=None):
        assert method == "GET"
        assert path == "/api/v2/adversaries"
        return {"ok": True, "json": self.payload}


def test_verify_adversary_exists_and_has_abilities():
    result = verify_adversary_exists(_FakeSession([
        {"adversary_id": "abc", "name": "sensor_to_edge", "atomic_ordering": [{"id": "ability-1"}]},
    ]), "abc")
    assert result["exists"] is True
    assert result["has_abilities"] is True


def test_verify_adversary_missing():
    result = verify_adversary_exists(_FakeSession([]), "missing")
    assert result["exists"] is False


def test_summarize_chain_counts_only_non_cleanup_abilities():
    summary = summarize_chain({
        "chain": [
            {"status": "success", "finish": True, "command": "run"},
            {"status": "success", "finish": True, "cleanup": True, "command": "cleanup"},
        ]
    })
    assert summary["ability_link_count"] == 1
    assert summary["successful_link_count"] == 1

