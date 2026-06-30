from __future__ import annotations

import json

from environment import attacker_env_controller, defender_env_controller


def test_defender_snapshot_cli(monkeypatch, capsys):
    monkeypatch.setattr(
        defender_env_controller.DefenderEnvController,
        "get_ryu_status",
        lambda self: {"active_actions": {}, "switches": []},
    )
    assert defender_env_controller.main(["--snapshot"]) == 0
    captured = capsys.readouterr().out
    assert json.loads(captured)["switches"] == []


def test_attacker_observe_cli(monkeypatch, capsys):
    monkeypatch.setattr(
        attacker_env_controller,
        "_get_json",
        lambda url, timeout: {"service": "caldera-dispatch-bridge"},
    )
    assert attacker_env_controller.main(["--observe"]) == 0
    captured = capsys.readouterr().out
    assert json.loads(captured)["service"] == "caldera-dispatch-bridge"
