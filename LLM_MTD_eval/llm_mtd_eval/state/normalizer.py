from __future__ import annotations

import re
from typing import Any

from ..settings import SUPPORTED_EXECUTABLE_ACTIONS
from ..types import (
    ActivePoolState,
    AttackContext,
    ControllerContext,
    NormalizedState,
    QosContext,
    SecurityContext,
)


METRIC_LINE = re.compile(
    r"^([a-zA-Z_:][\w:]*)(?:\{[^}]*\})?\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$"
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ok", "success"}
    return False


def parse_ryu_metrics(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = METRIC_LINE.match(line)
        if not match:
            continue
        values[match.group(1)] = _safe_float(match.group(2))
    return values


def _rows(core_data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = core_data.get(key, [])
    return value if isinstance(value, list) else []


def _series_rows(primary: dict[str, Any], secondary: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    primary_rows = _rows(primary, key)
    if primary_rows:
        return primary_rows
    if secondary:
        return _rows(secondary, key)
    return []


def _metric_value(
    rows: list[dict[str, Any]],
    *,
    source: str | None = None,
    role: str | None = None,
    metric: str | None = None,
    metric_suffix: str | None = None,
    default: float = 0.0,
) -> float:
    for row in rows:
        if source is not None and row.get("source") != source:
            continue
        if role is not None and row.get("role") != role:
            continue
        row_metric = str(row.get("metric", ""))
        if metric is not None and row_metric != metric:
            continue
        if metric_suffix is not None and not row_metric.endswith(metric_suffix):
            continue
        return _safe_float(row.get("value"), default)
    return default


def _scenario_rows(core_data: dict[str, Any], key: str, scenario_id: str) -> list[dict[str, Any]]:
    return [row for row in _rows(core_data, key) if row.get("source") == scenario_id]


def build_normalized_state(
    *,
    core_data: dict[str, Any],
    experiment_summary: dict[str, Any] | None = None,
    ryu_status_data: dict[str, Any],
    ryu_metrics_text: str,
    scenario: dict[str, Any],
    mulval_policy: dict[str, Any],
    active_pool_state: ActivePoolState,
    caldera_result: dict[str, Any] | None = None,
) -> NormalizedState:
    scenario_id = str(scenario.get("scenario_id", "unknown"))
    mulval_path = [str(item) for item in list(scenario.get("mulval_path") or [])]
    entry_node = str(scenario.get("entry_node") or (mulval_path[0] if mulval_path else ""))
    target_asset = str(scenario.get("target_asset") or (mulval_path[-1] if mulval_path else ""))

    path_key = "->".join(mulval_path)
    risk_score = _safe_float((mulval_policy.get("path_risk_scores") or {}).get(path_key), 0.5)

    live_series = core_data if core_data else (experiment_summary or {})
    attack_rows = _scenario_rows(live_series, "attack_events", scenario_id)
    defense_rows = _scenario_rows(live_series, "defense_events", scenario_id)
    message_rows = _series_rows(core_data, experiment_summary, "message_loss_counters")
    throughput_rows = _series_rows(core_data, experiment_summary, "throughput")
    sensor_edge_rows = _series_rows(core_data, experiment_summary, "sensor_to_edge_latency_ms")
    edge_cloud_rows = _series_rows(core_data, experiment_summary, "edge_to_cloud_latency_ms")

    gateway_node = mulval_path[1] if len(mulval_path) > 1 else ""
    worker_node = mulval_path[2] if len(mulval_path) > 2 else ""

    sensor_to_gateway_latency_ms = _metric_value(
        sensor_edge_rows,
        role="edge_gateway",
        metric_suffix="last_ingestion_latency_ms",
        default=0.0,
    )
    gateway_to_worker_latency_ms = _metric_value(
        _rows(core_data, "samples"),
        source=worker_node,
        role="edge_worker",
        metric="last_gateway_to_worker_latency_ms",
        default=0.0,
    )
    edge_to_cloud_latency_ms = _metric_value(
        edge_cloud_rows,
        role="cloud_db",
        metric_suffix="last_edge_to_cloud_latency_ms",
        default=0.0,
    )
    queue_length = int(
        round(
            _metric_value(
                message_rows,
                source=gateway_node,
                role="edge_gateway",
                metric_suffix="queue_length",
                default=0.0,
            )
        )
    )
    throughput_bps = 0.0
    for row in throughput_rows:
        metric_name = str(row.get("metric", ""))
        if "bytes" in metric_name and "per_second" in metric_name:
            throughput_bps += _safe_float(row.get("value"))

    generated_total = _metric_value(
        message_rows,
        source=entry_node,
        role="sensor",
        metric="generated_total",
        default=0.0,
    )
    received_total = _metric_value(
        message_rows,
        source=gateway_node,
        role="edge_gateway",
        metric=f"sensors.{entry_node}.received",
        default=0.0,
    )
    dropped_total = _metric_value(
        message_rows,
        source=gateway_node,
        role="edge_gateway",
        metric=f"sensors.{entry_node}.dropped",
        default=0.0,
    )
    if received_total + dropped_total > 0:
        message_loss_rate = dropped_total / max(received_total + dropped_total, 1.0)
    elif generated_total > 0:
        message_loss_rate = max(generated_total - received_total, 0.0) / generated_total
    else:
        message_loss_rate = 0.0

    attack_metrics = {str(row.get("metric", "")): row.get("value") for row in attack_rows}
    defense_metrics = {str(row.get("metric", "")): row.get("value") for row in defense_rows}

    ryu_metrics = parse_ryu_metrics(ryu_metrics_text)
    active_actions = (ryu_status_data.get("active_actions") or {}) if isinstance(ryu_status_data, dict) else {}

    caldera_result = caldera_result or {}

    return NormalizedState(
        scenario_id=scenario_id,
        timestamp=str(
            core_data.get("generated_at")
            or (experiment_summary or {}).get("generated_at")
            or scenario.get("timestamp")
            or ""
        ),
        target_asset=target_asset,
        entry_node=entry_node,
        attack_context=AttackContext(
            mulval_path=mulval_path,
            risk_score=risk_score,
            caldera_result=caldera_result,
        ),
        qos_context=QosContext(
            sensor_to_gateway_latency_ms=sensor_to_gateway_latency_ms,
            gateway_to_worker_latency_ms=gateway_to_worker_latency_ms,
            edge_to_cloud_latency_ms=edge_to_cloud_latency_ms,
            queue_length=queue_length,
            throughput_bps=throughput_bps,
            message_loss_rate=message_loss_rate,
        ),
        security_context=SecurityContext(
            gateway_seen=_bool_value(caldera_result.get("gateway_seen"))
            or _bool_value(attack_metrics.get("gateway_seen")),
            worker_seen=_bool_value(attack_metrics.get("worker_seen"))
            or _bool_value(attack_metrics.get("worker_requests_increase"))
            or _bool_value(caldera_result.get("worker_seen")),
            cloud_seen=_bool_value(attack_metrics.get("cloud_seen"))
            or _bool_value(attack_metrics.get("cloud_summary_rate_changes"))
            or _bool_value(caldera_result.get("cloud_seen")),
            attack_effect_success=_bool_value(attack_metrics.get("attack_effect_success"))
            or _bool_value(caldera_result.get("attack_effect_success")),
            defense_success=_bool_value(defense_metrics.get("defense_success"))
            or _bool_value(defense_metrics.get("success")),
        ),
        controller_context=ControllerContext(
            active_policy_actions=int(
                round(
                    _safe_float(
                        ryu_metrics.get("ryu_controller_active_policy_actions"),
                        float(len(active_actions)),
                    )
                )
            ),
            flow_rules_installed=int(
                round(_safe_float(ryu_metrics.get("ryu_controller_flow_rules_installed_total")))
            ),
            meters_added=int(round(_safe_float(ryu_metrics.get("ryu_controller_meters_added_total")))),
            ryu_apply_duration_ms=_safe_float(
                ryu_metrics.get("ryu_controller_last_action_duration_ms")
            ),
        ),
        allowed_actions=list(SUPPORTED_EXECUTABLE_ACTIONS),
        active_pool=active_pool_state,
    )
