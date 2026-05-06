from __future__ import annotations

import json

from llm_mtd_eval.llm_layer.defender_selector import select_defender_strategy
from llm_mtd_eval.types import LLMResponseTrace


def _active_defenders() -> list[dict[str, object]]:
    return [
        {
            "id": "D0_observe",
            "name": "Observe",
            "action": "observe",
            "target": "",
            "base_cost": 0.0,
            "expected_effects": [],
            "action_payload": {"action": "observe"},
        },
        {
            "id": "D1_quarantine_sen4",
            "name": "Quarantine sen4",
            "action": "quarantine_sensor",
            "target": "sen4",
            "base_cost": 0.6,
            "expected_effects": ["drop_rules_active"],
            "action_payload": {"action": "quarantine_sensor", "target": "sen4"},
        },
    ]


def _game_result() -> dict[str, object]:
    return {
        "defender_population": {
            "D0_observe": 0.2,
            "D1_quarantine_sen4": 0.8,
        },
        "defender_utilities": {
            "D0_observe": 0.1,
            "D1_quarantine_sen4": 0.9,
        },
    }


def test_defender_selector_returns_active_strategy_id(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        captured["user_prompt"] = user_prompt
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-model",
            raw_text=json.dumps(
                {
                    "selected_defender_strategy_id": "D1_quarantine_sen4",
                    "ranked_candidates": [
                        {"strategy_id": "D1_quarantine_sen4", "reason": "Entry-node containment"},
                        {"strategy_id": "D0_observe", "reason": "Too passive while attack is active"},
                    ],
                    "baseline_alignment": "followed",
                    "override_reason": "Matched the baseline top-utility defender.",
                    "urgency_level": "high",
                    "confidence": 0.88,
                    "reasoning_summary": "Contain the compromised entry node.",
                    "expected_security_gain": 0.84,
                    "expected_qos_impact": 0.36,
                    "expected_controller_cost": 0.22,
                }
            ),
            latency_ms=12.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model"},
        live_state={
            "scenario_id": "sen4_edge2_clouddb",
            "entry_node": "sen4",
            "target_asset": "cloud_db",
            "attack_active": True,
            "path_stage": 2,
            "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
            "qos": {"sensor_to_edge_latency_ms": 3.0, "edge_to_cloud_latency_ms": 2.0, "loss_rate": 0.0},
            "overhead": {"controller_active_actions": 0, "flow_rules_installed": 0, "meters_added": 0, "controller_apply_ms": 1.0},
            "defender_observation": {"worker_seen": True},
        },
        selected_attacker={"id": "A2_sensor_http_abuse_sen4"},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
        stage_memory={
            "previous_stage_id": 2,
            "previous_attacker_strategy_id": "A1_sensor_probe_sen4",
            "previous_defender_strategy_id": "D0_observe",
            "previous_defense_confirmed": False,
            "previous_attack_progression_continued": True,
            "recent_qos_deltas": {"sensor_to_edge_latency_ms_delta": 0.8},
            "recent_controller_deltas": {"flow_rules_installed_delta": 0.0},
        },
    )

    assert result.selection["id"] == "D1_quarantine_sen4"
    assert result.fallback_used is False
    assert result.selection["reasoning_summary"] == "Contain the compromised entry node."
    assert result.selection["baseline_alignment"] == "followed"
    assert result.raw_selected_strategy_id == "D1_quarantine_sen4"
    assert result.ranked_candidates[0]["strategy_id"] == "D1_quarantine_sen4"
    assert result.stage_memory_used is True
    assert "previous_stage_memory" in captured["user_prompt"]
    assert "active_defender_candidates" in captured["user_prompt"]
    assert "telemetry_quality" in captured["user_prompt"]


def test_defender_selector_injects_low_confidence_telemetry_context(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-model",
            raw_text=json.dumps(
                {
                    "selected_defender_strategy_id": "D0_observe",
                    "ranked_candidates": [
                        {"strategy_id": "D0_observe", "llm_rank": 1},
                        {"strategy_id": "D1_quarantine_sen4", "llm_rank": 2},
                    ],
                    "reasoning_summary": "Telemetry is low confidence, so observation is the least disruptive sufficient action.",
                    "decision_mode": "uncertainty_limited",
                    "telemetry_confidence": "low",
                    "baseline_alignment": "overrode",
                    "override_reason": "Low-confidence telemetry made containment premature.",
                    "repeat_previous_action": False,
                    "why_not_observe": "Observe is selected.",
                    "why_not_rate_limit": "No confirmed live progression justifies rate limiting.",
                    "why_not_quarantine": "No confirmed live progression justifies quarantine.",
                    "urgency_level": "low",
                    "confidence": 0.62,
                    "expected_security_gain": 0.26,
                    "expected_qos_impact": 0.01,
                    "expected_controller_cost": 0.0,
                }
            ),
            latency_ms=9.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model"},
        live_state={
            "scenario_id": "sen4_edge2_clouddb",
            "entry_node": "sen4",
            "target_asset": "cloud_db",
            "attack_active": False,
            "attack_effect_success": False,
            "gateway_seen": False,
            "worker_seen": False,
            "cloud_seen": False,
            "path_stage": 0,
            "source_errors": ["core_fetch_failed"],
            "workload": {"generated_total": 0.0, "gateway_received": 0.0},
            "defender_observation": {"attack_metrics": {}, "defense_metrics": {}},
        },
        selected_attacker={"id": "A2_sensor_http_abuse_sen4"},
        attacker_execution={"status": "dispatched"},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
        stage_memory={
            "previous_defender_strategy_id": "D1_quarantine_sen4",
            "previous_defense_success": False,
        },
    )

    payload = json.loads(captured["user_prompt"].split("\n\n", 1)[1])
    assert payload["telemetry_quality"]["level"] == "low"
    assert "source_errors_present" in payload["telemetry_quality"]["reasons"]
    assert "all_zero_workload" in payload["telemetry_quality"]["reasons"]
    assert payload["current_evidence"]["attack_dispatch_status"] == "dispatched"
    assert any(
        candidate["strategy_id"] == "D1_quarantine_sen4" and candidate["previous_failed_same_action"] is True
        for candidate in payload["active_defender_candidates"]
    )
    assert "least disruptive" in captured["system_prompt"]
    assert result.selection["id"] == "D0_observe"
    assert result.selection["decision_mode"] == "uncertainty_limited"
    assert result.selection["telemetry_confidence"] == "low"
    assert result.repeat_previous_action is False
    assert result.why_not_quarantine.startswith("No confirmed")


def test_defender_selector_falls_back_when_llm_returns_invalid_id(monkeypatch) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-model",
            raw_text=json.dumps(
                {
                    "selected_defender_strategy_id": "D9_invented",
                    "ranked_candidates": ["D9_invented", "D1_quarantine_sen4"],
                    "confidence": 0.51,
                    "reasoning_summary": "Invalid choice.",
                    "expected_security_gain": 0.1,
                    "expected_qos_impact": 0.1,
                    "expected_controller_cost": 0.1,
                }
            ),
            latency_ms=8.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model"},
        live_state={"scenario_id": "sen4_edge2_clouddb", "entry_node": "sen4", "target_asset": "cloud_db"},
        selected_attacker={"id": "A2_sensor_http_abuse_sen4"},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
    )

    assert result.selection["id"] == "D1_quarantine_sen4"
    assert result.fallback_used is True
    assert result.fallback_reason.startswith("invalid_defender_strategy_id")
    assert result.selection["mode"] == "llm_defender_recovered"
    assert result.recovery_used is True
    assert result.raw_selected_strategy_id == "D9_invented"
    assert result.executed_via_fallback is True


def test_defender_selector_recovers_from_malformed_json(monkeypatch) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-model",
            raw_text='{bad json "selected_defender_strategy_id" "D1_quarantine_sen4" ranked_candidates ["D1_quarantine_sen4","D0_observe"] confidence 0.82 reasoning_summary "Quarantine sen4 because the entry node is under attack." expected_security_gain 0.77 expected_qos_impact 0.31 expected_controller_cost 0.22}',
            latency_ms=11.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model"},
        live_state={"scenario_id": "sen4_edge2_clouddb", "entry_node": "sen4", "target_asset": "cloud_db"},
        selected_attacker={"id": "A2_sensor_http_abuse_sen4"},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
    )

    assert result.selection["id"] == "D1_quarantine_sen4"
    assert result.fallback_used is True
    assert result.fallback_reason.startswith("malformed_response_json")
    assert result.selection["mode"] == "llm_defender_recovered"
    assert result.recovery_used is True
    assert result.raw_selected_strategy_id == "D1_quarantine_sen4"


def test_defender_selector_discourages_observe_when_attack_has_progressed(monkeypatch) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-model",
            raw_text=json.dumps(
                {
                    "selected_defender_strategy_id": "D0_observe",
                    "ranked_candidates": ["D0_observe", "D1_quarantine_sen4"],
                    "baseline_alignment": "overrode",
                    "override_reason": "Observe is safer for QoS.",
                    "urgency_level": "high",
                    "confidence": 0.67,
                    "reasoning_summary": "Observe for now.",
                    "expected_security_gain": 0.1,
                    "expected_qos_impact": 0.02,
                    "expected_controller_cost": 0.0,
                }
            ),
            latency_ms=10.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model"},
        live_state={
            "scenario_id": "sen4_edge2_clouddb",
            "entry_node": "sen4",
            "target_asset": "cloud_db",
            "attack_active": True,
            "path_stage": 3,
            "current_path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"],
            "defender_observation": {"cloud_seen": True},
            "qos": {"sensor_to_edge_latency_ms": 3.0, "edge_to_cloud_latency_ms": 2.0, "loss_rate": 0.0},
            "overhead": {"controller_active_actions": 0, "flow_rules_installed": 0, "meters_added": 0, "controller_apply_ms": 1.0},
        },
        selected_attacker={"id": "A2_sensor_http_abuse_sen4", "path": ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"]},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
        stage_memory={"previous_attack_progression_continued": True},
    )

    assert result.selection["id"] == "D1_quarantine_sen4"
    assert result.fallback_used is True
    assert result.fallback_reason == "observe_disallowed_high_urgency"
    assert result.raw_selected_strategy_id == "D0_observe"
    assert result.selection["reasoning_summary"].startswith("The raw LLM selection D0_observe was disallowed")
    assert result.executed_via_fallback is True
    assert result.fallback_constraint_name == "observe_disallowed_high_urgency"


def test_defender_selector_retries_with_compact_prompt_after_timeout(monkeypatch) -> None:
    calls: list[str] = []

    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        calls.append(user_prompt)
        if len(calls) == 1:
            raise RuntimeError("Ollama completion failed: timed out")
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-model",
            raw_text=json.dumps(
                {
                    "selected_defender_strategy_id": "D1_quarantine_sen4",
                    "ranked_candidates": ["D1_quarantine_sen4", "D0_observe"],
                    "baseline_alignment": "followed",
                    "override_reason": "Matched the baseline top defender.",
                    "urgency_level": "high",
                    "confidence": 0.8,
                    "reasoning_summary": "Compact retry selected quarantine because path_stage is high.",
                    "expected_security_gain": 0.8,
                    "expected_qos_impact": 0.3,
                    "expected_controller_cost": 0.2,
                }
            ),
            latency_ms=5.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model", "timeout_seconds": 1, "max_retries": 0},
        live_state={
            "scenario_id": "sen4_edge2_clouddb",
            "entry_node": "sen4",
            "target_asset": "cloud_db",
            "attack_active": True,
            "path_stage": 2,
        },
        selected_attacker={"id": "A2_sensor_http_abuse_sen4"},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
    )

    assert len(calls) == 2
    assert "Prompt mode: compact" in calls[1]
    assert result.request_success is True
    assert result.parse_success is True
    assert result.selection["id"] == "D1_quarantine_sen4"


def test_defender_selector_timeout_failure_is_marked_debug_only(monkeypatch) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        raise RuntimeError("Ollama completion failed: timed out")

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.defender_selector.LLMClient.complete_json",
        fake_complete_json,
    )

    result = select_defender_strategy(
        llm_config={"provider": "ollama", "model_name": "fake-model", "timeout_seconds": 1, "max_retries": 0},
        live_state={"scenario_id": "sen4_edge2_clouddb", "entry_node": "sen4", "target_asset": "cloud_db"},
        selected_attacker={"id": "A2_sensor_http_abuse_sen4"},
        active_defenders=_active_defenders(),
        game_result=_game_result(),
    )

    assert result.request_success is False
    assert result.parse_success is False
    assert result.fallback_used is True
    assert "timed out" in result.request_error
    assert result.trace.latency_ms >= 0.0
