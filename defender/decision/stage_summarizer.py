from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from eval.types import LLMResponseTrace
from .llm_client import LLMClient
from .response_parser import extract_first_json_object


@dataclass(slots=True)
class StageSummaryResult:
    record: dict[str, Any]
    trace: LLMResponseTrace | None
    fallback_used: bool


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _delta(previous: dict[str, Any] | None, next_state: dict[str, Any] | None, path: tuple[str, ...]) -> float:
    def lookup(state: dict[str, Any] | None) -> float:
        current: Any = state or {}
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = None
        return _safe_float(current, 0.0)

    return round(lookup(next_state) - lookup(previous), 6)


def _normalized_controller_delta(
    previous_state: dict[str, Any],
    next_state: dict[str, Any] | None,
) -> dict[str, float]:
    return {
        "active_policy_actions_delta": _delta(previous_state, next_state, ("overhead", "controller_active_actions")),
        "flow_rules_installed_delta": _delta(previous_state, next_state, ("overhead", "flow_rules_installed")),
        "meters_added_delta": _delta(previous_state, next_state, ("overhead", "meters_added")),
        "controller_apply_ms_delta": _delta(previous_state, next_state, ("overhead", "controller_apply_ms")),
        "flow_delete_commands_delta": _delta(previous_state, next_state, ("overhead", "flow_delete_commands")),
        "total_cpu_seconds_delta": _delta(previous_state, next_state, ("overhead", "total_cpu_seconds")),
        "total_memory_kb_delta": _delta(previous_state, next_state, ("overhead", "total_memory_kb")),
    }


def _normalize_summary_text(value: Any, fallback: str) -> str:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(parts) if parts else fallback
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return fallback
        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                parsed = ast.literal_eval(cleaned)
            except (ValueError, SyntaxError):
                return cleaned
            if isinstance(parsed, list):
                parts = [str(item).strip() for item in parsed if str(item).strip()]
                return " ".join(parts) if parts else fallback
        return cleaned
    return fallback


def _normalize_controller_delta_payload(
    value: Any,
    fallback: dict[str, float],
) -> dict[str, float]:
    if not isinstance(value, dict):
        return fallback

    aliases = {
        "active_policy_actions_delta": "active_policy_actions_delta",
        "active_actions_delta": "active_policy_actions_delta",
        "flow_rules_installed_delta": "flow_rules_installed_delta",
        "flow_delta": "flow_rules_installed_delta",
        "meters_added_delta": "meters_added_delta",
        "meter_delta": "meters_added_delta",
        "controller_apply_ms_delta": "controller_apply_ms_delta",
        "apply_ms_delta": "controller_apply_ms_delta",
        "flow_delete_commands_delta": "flow_delete_commands_delta",
        "total_cpu_seconds_delta": "total_cpu_seconds_delta",
        "total_memory_kb_delta": "total_memory_kb_delta",
    }
    normalized = dict(fallback)
    for key, target in aliases.items():
        if key in value:
            normalized[target] = _safe_float(value.get(key), normalized[target])
    return normalized


def _normalize_qos_delta_payload(value: Any, fallback: dict[str, float]) -> dict[str, float]:
    canonical_fallback = {
        "sensor_to_edge_latency_ms": _safe_float(
            fallback.get("sensor_to_edge_latency_ms", fallback.get("sensor_to_edge_latency_ms_delta"))
        ),
        "edge_to_cloud_latency_ms": _safe_float(
            fallback.get("edge_to_cloud_latency_ms", fallback.get("edge_to_cloud_latency_ms_delta"))
        ),
        "throughput_bytes_per_second": _safe_float(
            fallback.get("throughput_bytes_per_second", fallback.get("throughput_bytes_per_second_delta"))
        ),
        "loss_rate": _safe_float(fallback.get("loss_rate", fallback.get("loss_rate_delta"))),
    }
    if not isinstance(value, dict):
        return canonical_fallback
    aliases = {
        "sensor_to_edge_latency_ms": "sensor_to_edge_latency_ms",
        "sensor_to_edge_latency_ms_delta": "sensor_to_edge_latency_ms",
        "edge_to_cloud_latency_ms": "edge_to_cloud_latency_ms",
        "edge_to_cloud_latency_ms_delta": "edge_to_cloud_latency_ms",
        "throughput_bytes_per_second": "throughput_bytes_per_second",
        "throughput_bytes_per_second_delta": "throughput_bytes_per_second",
        "loss_rate": "loss_rate",
        "loss_rate_delta": "loss_rate",
    }
    normalized = canonical_fallback
    for key, target in aliases.items():
        if key in value:
            normalized[target] = _safe_float(value.get(key), normalized.get(target, 0.0))
    return normalized


def _summary_fallback_text(
    stage_id: int,
    scenario_id: str,
    attacker_strategy_id: str,
    defender_strategy_id: str,
    reasoning_summary: str,
    next_state: dict[str, Any] | None,
    execution: dict[str, Any],
) -> str:
    state = next_state or {}
    attack_active = bool(state.get("attack_active"))
    attack_effect_success = bool(state.get("attack_effect_success"))
    defense_success = bool(state.get("defense_success"))
    defense_confirmed = bool(execution.get("defense_confirmed"))
    defense_effects_confirmed = bool(execution.get("defense_effects_confirmed"))
    stage_valid = bool(execution.get("stage_valid"))
    defender_status = str(execution.get("defender_status") or "not_executed")
    defender_action = str(execution.get("defender_action") or "observe")
    path_stage = int(state.get("path_stage") or 0)
    if not stage_valid:
        outcome = "this stage was recorded for visibility only and excluded from learning"
    elif defense_confirmed and defense_effects_confirmed and defense_success:
        outcome = "defense containment observed"
    elif attack_active or attack_effect_success or path_stage >= 1:
        outcome = "attack remained active after the defender stage"
    else:
        outcome = "no active attack indicators remained after the stage"
    return (
        f"Stage {stage_id} for {scenario_id}: attacker {attacker_strategy_id} met defender "
        f"{defender_strategy_id}. Defender action {defender_action} ended with execution status "
        f"{defender_status}, defense_confirmed={defense_confirmed}, defense_success={defense_success}. "
        f"Rationale: {reasoning_summary} After execution the path stage was {path_stage}, and the observed "
        f"outcome was {outcome}."
    )


def build_stage_summary_record(
    *,
    stage_id: int,
    scenario_id: str,
    attacker_strategy_id: str,
    defender_strategy_id: str,
    reasoning_summary: str,
    previous_state: dict[str, Any],
    next_state: dict[str, Any] | None,
    execution: dict[str, Any],
    llm_config: dict[str, Any],
    summary_template_path: Path | None = None,
) -> StageSummaryResult:
    security_outcome = {
        "attack_effect_success": bool((next_state or previous_state).get("attack_effect_success")),
        "defense_success": bool((next_state or previous_state).get("defense_success")),
    }
    qos_delta = {
        "sensor_to_edge_latency_ms_delta": _delta(previous_state, next_state, ("qos", "sensor_to_edge_latency_ms")),
        "edge_to_cloud_latency_ms_delta": _delta(previous_state, next_state, ("qos", "edge_to_cloud_latency_ms")),
        "throughput_bytes_per_second_delta": _delta(previous_state, next_state, ("workload", "throughput_bytes_per_second")),
        "loss_rate_delta": _delta(previous_state, next_state, ("qos", "loss_rate")),
    }
    controller_delta = {
        **_normalized_controller_delta(previous_state, next_state),
    }

    fallback_record = {
        "stage_id": stage_id,
        "scenario_id": scenario_id,
        "decision_source": "llm_defender",
        "attacker_strategy_id": attacker_strategy_id,
        "defender_strategy_id": defender_strategy_id,
        "summary_text": _summary_fallback_text(
            stage_id,
            scenario_id,
            attacker_strategy_id,
            defender_strategy_id,
            reasoning_summary,
            next_state,
            execution,
        ),
        "security_outcome": security_outcome,
        "qos_delta": qos_delta,
        "controller_delta": controller_delta,
        "execution": execution,
    }

    provider = str(llm_config.get("provider", "mock"))
    if provider == "mock":
        return StageSummaryResult(record=fallback_record, trace=None, fallback_used=True)

    user_payload = {
        "stage_id": stage_id,
        "scenario_id": scenario_id,
        "attacker_strategy_id": attacker_strategy_id,
        "defender_strategy_id": defender_strategy_id,
        "reasoning_summary": reasoning_summary,
        "previous_state_summary": {
            "path_stage": previous_state.get("path_stage"),
            "attack_active": previous_state.get("attack_active"),
            "attack_effect_success": previous_state.get("attack_effect_success"),
            "defense_active": previous_state.get("defense_active"),
            "defense_success": previous_state.get("defense_success"),
            "qos": previous_state.get("qos", {}) or {},
            "overhead": previous_state.get("overhead", {}) or {},
        },
        "next_state_summary": {
            "path_stage": (next_state or {}).get("path_stage"),
            "attack_active": (next_state or {}).get("attack_active"),
            "attack_effect_success": (next_state or {}).get("attack_effect_success"),
            "defense_active": (next_state or {}).get("defense_active"),
            "defense_success": (next_state or {}).get("defense_success"),
            "qos": (next_state or {}).get("qos", {}) or {},
            "overhead": (next_state or {}).get("overhead", {}) or {},
        },
        "execution": execution,
        "required_schema": {
            "summary_text": "string",
            "security_outcome": {
                "attack_effect_success": "boolean",
                "defense_success": "boolean",
            },
            "qos_delta": "object",
            "controller_delta": "object",
        },
    }
    template_prefix = ""
    if summary_template_path and summary_template_path.exists():
        template_prefix = summary_template_path.read_text(encoding="utf-8").strip() + "\n\n"
    user_prompt = (
        f"{template_prefix}Return strict JSON with keys summary_text, security_outcome, qos_delta, controller_delta.\n\n"
        f"Stage context:\n{json.dumps(user_payload, indent=2, sort_keys=True)}"
    )

    llm_client = LLMClient(llm_config)
    try:
        trace = llm_client.complete_json(
            "You summarize one live attacker-defender stage for dashboard display. Return strict JSON only.",
            user_prompt,
        )
    except Exception:
        return StageSummaryResult(record=fallback_record, trace=None, fallback_used=True)
    try:
        parsed = json.loads(extract_first_json_object(trace.raw_text))
        if not isinstance(parsed, dict):
            raise ValueError("stage summary response was not a JSON object")
    except Exception:
        return StageSummaryResult(record=fallback_record, trace=trace, fallback_used=True)

    record = {
        **fallback_record,
        "summary_text": (
            fallback_record["summary_text"]
            if (not execution.get("stage_valid", True) or not execution.get("defense_confirmed", False))
            else _normalize_summary_text(parsed.get("summary_text"), fallback_record["summary_text"])
        ),
        "security_outcome": parsed.get("security_outcome") if isinstance(parsed.get("security_outcome"), dict) else security_outcome,
        "qos_delta": _normalize_qos_delta_payload(parsed.get("qos_delta"), qos_delta),
        "controller_delta": _normalize_controller_delta_payload(parsed.get("controller_delta"), controller_delta),
    }
    return StageSummaryResult(record=record, trace=trace, fallback_used=False)
