from __future__ import annotations

import json

from llm_mtd_eval.llm_layer.stage_summarizer import build_stage_summary_record
from llm_mtd_eval.types import LLMResponseTrace


def test_stage_summarizer_accepts_llm_json(monkeypatch, tmp_path) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-summary-model",
            raw_text=json.dumps(
                {
                    "summary_text": "The defender isolated sen4 and containment was observed.",
                    "security_outcome": {
                        "attack_effect_success": False,
                        "defense_success": True,
                    },
                    "qos_delta": {
                        "sensor_to_edge_latency_ms_delta": 0.6,
                        "throughput_bytes_per_second_delta": -240.0,
                    },
                    "controller_delta": {
                        "active_policy_actions_delta": 1,
                        "flow_rules_installed_delta": 18,
                        "meters_added_delta": 0,
                    },
                }
            ),
            latency_ms=15.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.stage_summarizer.LLMClient.complete_json",
        fake_complete_json,
    )

    summary = build_stage_summary_record(
        stage_id=4,
        scenario_id="sen4_edge2_clouddb",
        attacker_strategy_id="A2_sensor_http_abuse_sen4",
        defender_strategy_id="D1_quarantine_sen4",
        reasoning_summary="Quarantine the entry node to stop the attack path.",
        previous_state={
            "path_stage": 2,
            "attack_effect_success": False,
            "defense_success": False,
            "qos": {"sensor_to_edge_latency_ms": 2.0, "edge_to_cloud_latency_ms": 1.5, "loss_rate": 0.0},
            "workload": {"throughput_bytes_per_second": 1024.0},
            "overhead": {"controller_active_actions": 0, "flow_rules_installed": 0, "meters_added": 0},
        },
        next_state={
            "path_stage": 1,
            "attack_effect_success": False,
            "defense_success": True,
            "qos": {"sensor_to_edge_latency_ms": 2.6, "edge_to_cloud_latency_ms": 1.7, "loss_rate": 0.01},
            "workload": {"throughput_bytes_per_second": 784.0},
            "overhead": {"controller_active_actions": 1, "flow_rules_installed": 18, "meters_added": 0},
        },
        execution={
            "attacker_status": "dispatched",
            "defender_status": "executed",
            "defense_confirmed": True,
            "stage_valid": True,
        },
        llm_config={"provider": "ollama", "model_name": "fake-summary-model"},
        summary_template_path=tmp_path / "summary_template.txt",
    )

    assert summary.fallback_used is False
    assert summary.record["summary_text"] == "The defender isolated sen4 and containment was observed."
    assert summary.record["security_outcome"]["defense_success"] is True


def test_stage_summarizer_keeps_fallback_text_for_invalid_stage(monkeypatch, tmp_path) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-summary-model",
            raw_text=json.dumps(
                {
                    "summary_text": "Attack pressure was reduced.",
                    "security_outcome": {
                        "attack_effect_success": True,
                        "defense_success": False,
                    },
                    "qos_delta": {},
                    "controller_delta": {},
                }
            ),
            latency_ms=10.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.stage_summarizer.LLMClient.complete_json",
        fake_complete_json,
    )

    summary = build_stage_summary_record(
        stage_id=5,
        scenario_id="sen4_edge2_clouddb",
        attacker_strategy_id="A2_sensor_http_abuse_sen4",
        defender_strategy_id="D0_observe",
        reasoning_summary="Observe because no valid parsed LLM decision was available.",
        previous_state={"path_stage": 2, "attack_active": True, "attack_effect_success": False},
        next_state={"path_stage": 3, "attack_active": True, "attack_effect_success": True, "defense_success": False},
        execution={"attacker_status": "dispatched", "defender_status": "observe_only", "defense_confirmed": False, "stage_valid": False},
        llm_config={"provider": "ollama", "model_name": "fake-summary-model"},
        summary_template_path=tmp_path / "summary_template.txt",
    )

    assert summary.record["summary_text"] != "Attack pressure was reduced."
    assert "recorded for visibility only" in summary.record["summary_text"]


def test_stage_summarizer_normalizes_list_like_summary_text(monkeypatch, tmp_path) -> None:
    def fake_complete_json(self, system_prompt, user_prompt, state=None):  # noqa: ANN001
        return LLMResponseTrace(
            provider="ollama",
            model_name="fake-summary-model",
            raw_text=json.dumps(
                {
                    "summary_text": "['Attacker dispatched from sen4.', 'Defender quarantined sen4.']",
                    "security_outcome": {
                        "attack_effect_success": False,
                        "defense_success": True,
                    },
                    "qos_delta": {},
                    "controller_delta": {
                        "active_actions_delta": 1,
                        "apply_ms_delta": 0.8,
                    },
                }
            ),
            latency_ms=12.0,
            retries_used=0,
            prompt_preview=user_prompt[:120],
        )

    monkeypatch.setattr(
        "llm_mtd_eval.llm_layer.stage_summarizer.LLMClient.complete_json",
        fake_complete_json,
    )

    summary = build_stage_summary_record(
        stage_id=6,
        scenario_id="sen4_edge2_clouddb",
        attacker_strategy_id="A2_sensor_http_abuse_sen4",
        defender_strategy_id="D1_quarantine_sen4",
        reasoning_summary="Quarantine sen4 because the attack path starts there.",
        previous_state={"path_stage": 2, "qos": {}, "workload": {}, "overhead": {}},
        next_state={"path_stage": 1, "attack_effect_success": False, "defense_success": True, "qos": {}, "workload": {}, "overhead": {}},
        execution={
            "attacker_status": "dispatched",
            "defender_status": "executed",
            "defense_confirmed": True,
            "defense_effects_confirmed": True,
            "stage_valid": True,
        },
        llm_config={"provider": "ollama", "model_name": "fake-summary-model"},
        summary_template_path=tmp_path / "summary_template.txt",
    )

    assert summary.record["summary_text"] == "Attacker dispatched from sen4. Defender quarantined sen4."
    assert summary.record["controller_delta"]["active_policy_actions_delta"] == 1.0
    assert summary.record["controller_delta"]["controller_apply_ms_delta"] == 0.8
