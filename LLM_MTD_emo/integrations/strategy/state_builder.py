#!/usr/bin/env python3
"""Build the S_t state object consumed by the strategy layer.

The builder reads the same live surfaces already used by the dashboard:

- cloud_metrics /core
- Ryu /mtd/metrics
- optional Ryu /mtd/status topology state
- optional MulVAL policy JSON

It is intentionally tolerant of missing live services so the strategy model can
still be inspected offline.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_REGISTRY = PROJECT_ROOT / "integrations" / "attack_scenarios.json"
DEFAULT_MULVAL_POLICY = (
    PROJECT_ROOT / "integrations" / "mulval" / "outputs" / "base_edge2_policy.json"
)
DEFAULT_CORE_URL = "http://127.0.0.1:8088/core"
DEFAULT_MTD_METRICS_URL = "http://127.0.0.1:8080/mtd/metrics"
DEFAULT_MTD_STATUS_URL = "http://127.0.0.1:8080/mtd/status"
DEFAULT_CLOUD_METRICS_CONTAINER = os.environ.get(
    "STRATEGY_CLOUD_METRICS_CONTAINER",
    "mn.cloud_metrics",
)
DEFAULT_CORE_DOCKER_FALLBACK = os.environ.get(
    "STRATEGY_CORE_DOCKER_FALLBACK",
    "1",
).strip().lower() not in ("0", "false", "no")

METRIC_LINE = re.compile(
    r"^([a-zA-Z_:][\w:]*)(?:\{([^}]*)\})?\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[+-]?Inf|NaN)$"
)
LABEL_PATTERN = re.compile(r'([a-zA-Z_][\w]*)="((?:\\.|[^"\\])*)"')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    return parsed


def safe_int(value: Any, default: int = 0) -> int:
    return int(round(safe_float(value, float(default))))


def bool_metric(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return safe_float(value) > 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ok", "success")
    return False


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def load_json_file(path: Optional[Path], default: Any = None) -> Any:
    if path is None or not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fetch_text(url: str, timeout: float = 2.0) -> str:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: float = 2.0) -> Dict[str, Any]:
    return json.loads(fetch_text(url, timeout=timeout) or "{}")


def try_fetch_json(url: Optional[str], timeout: float, errors: List[str]) -> Dict[str, Any]:
    if not url:
        return {}
    try:
        return fetch_json(url, timeout=timeout)
    except Exception as error:  # pragma: no cover - live lab dependent
        errors.append(f"json fetch failed for {url}: {error}")
        return {}


def try_fetch_text(url: Optional[str], timeout: float, errors: List[str]) -> str:
    if not url:
        return ""
    try:
        return fetch_text(url, timeout=timeout)
    except Exception as error:  # pragma: no cover - live lab dependent
        errors.append(f"text fetch failed for {url}: {error}")
        return ""


def fetch_json_from_container(
    container_name: str,
    path: str,
    timeout: float,
    errors: List[str],
) -> Dict[str, Any]:
    """Fetch JSON from a service listening on localhost inside a Docker container."""
    if not container_name:
        return {}

    target_url = f"http://127.0.0.1:8000{path}"
    python_code = (
        "import sys, urllib.request\n"
        "with urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])) as r:\n"
        "    sys.stdout.buffer.write(r.read())\n"
    )
    commands = [
        ["docker", "exec", container_name, "python3", "-c", python_code, target_url, str(timeout)],
        ["docker", "exec", container_name, "python", "-c", python_code, target_url, str(timeout)],
        ["docker", "exec", container_name, "curl", "-fsS", target_url],
    ]

    attempt_errors = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(timeout + 4.0, 6.0),
            )
        except Exception as error:  # pragma: no cover - deployment dependent
            attempt_errors.append(f"{command[3]} failed to start: {error}")
            continue

        if result.returncode == 0:
            try:
                return json.loads(result.stdout.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError as error:
                attempt_errors.append(f"{command[3]} returned invalid json: {error}")
                continue

        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or f"exit code {result.returncode}"
        )
        attempt_errors.append(f"{command[3]}: {detail}")

    errors.append(
        f"docker json fetch failed for {container_name}:{path}: "
        + " | ".join(attempt_errors)
    )
    return {}


def parse_prometheus_text(text: str) -> Dict[str, Any]:
    metrics: Dict[str, float] = {}
    series: List[Dict[str, Any]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = METRIC_LINE.match(line)
        if not match:
            continue

        name, label_text, raw_value = match.groups()
        labels = parse_prometheus_labels(label_text or "")
        value = safe_float(raw_value)
        point = {"name": name, "labels": labels, "value": value}
        series.append(point)
        if not labels:
            metrics[name] = value

    return {"metrics": metrics, "series": series, "raw": text}


def parse_prometheus_labels(label_text: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for match in LABEL_PATTERN.finditer(label_text):
        labels[match.group(1)] = (
            match.group(2)
            .replace(r"\\", "\\")
            .replace(r"\n", "\n")
            .replace(r"\"", '"')
        )
    return labels


def rows(data: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    value = data.get(key, [])
    return value if isinstance(value, list) else []


def metric_value(
    metric_rows: Iterable[Dict[str, Any]],
    source: Optional[str] = None,
    metric: Optional[str] = None,
    role: Optional[str] = None,
    default: Optional[float] = None,
) -> Optional[float]:
    for row in metric_rows:
        if source is not None and row.get("source") != source:
            continue
        if role is not None and row.get("role") != role:
            continue
        if metric is not None and row.get("metric") != metric:
            continue
        return safe_float(row.get("value"))
    return default


def sum_metrics(
    metric_rows: Iterable[Dict[str, Any]],
    predicate: Callable[[Dict[str, Any]], bool],
) -> float:
    return sum(safe_float(row.get("value")) for row in metric_rows if predicate(row))


def avg_metrics(
    metric_rows: Iterable[Dict[str, Any]],
    predicate: Callable[[Dict[str, Any]], bool],
) -> float:
    values = [safe_float(row.get("value")) for row in metric_rows if predicate(row)]
    if not values:
        return 0.0
    return sum(values) / len(values)


def metric_map(metric_rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for row in metric_rows:
        metric_name = row.get("metric")
        if metric_name:
            result[str(metric_name)] = safe_float(row.get("value"))
    return result


def scenario_by_id(scenarios: Iterable[Dict[str, Any]], scenario_id: str) -> Dict[str, Any]:
    for scenario in scenarios:
        if scenario.get("scenario_id") == scenario_id:
            return scenario
    return {}


def choose_scenario_id(
    core_data: Dict[str, Any],
    scenarios: List[Dict[str, Any]],
    mulval_policy: Dict[str, Any],
    requested_scenario_id: Optional[str] = None,
) -> str:
    if requested_scenario_id:
        return requested_scenario_id

    scores: Dict[str, float] = {}
    for row in rows(core_data, "attack_events") + rows(core_data, "defense_events"):
        source = str(row.get("source", "") or "")
        if not source or source == "unknown":
            continue
        scores[source] = scores.get(source, 0.0) + max(safe_float(row.get("value")), 0.0)
        if row.get("metric") == "attack_active" and bool_metric(row.get("value")):
            scores[source] += 1000.0
        if row.get("metric") in ("success", "defense_success") and bool_metric(row.get("value")):
            scores[source] += 80.0

    known_ids = {str(scenario.get("scenario_id")) for scenario in scenarios}
    ranked_known = [
        item for item in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        if item[0] in known_ids
    ]
    if ranked_known:
        return ranked_known[0][0]

    policy_scenario_id = str(mulval_policy.get("scenario_id", "") or "")
    if policy_scenario_id in known_ids:
        return policy_scenario_id

    policy_paths = mulval_policy.get("attack_paths") or []
    if policy_paths:
        first_policy_path = list(policy_paths[0])
        for scenario in scenarios:
            if scenario.get("mulval_path") == first_policy_path:
                return str(scenario.get("scenario_id"))

    return str(scenarios[0].get("scenario_id")) if scenarios else "unknown"


def active_rows_for_scenario(
    core_data: Dict[str, Any],
    group_name: str,
    scenario_id: str,
) -> List[Dict[str, Any]]:
    return [row for row in rows(core_data, group_name) if row.get("source") == scenario_id]


def path_stage_label(path_stage: int) -> str:
    labels = {
        0: "none",
        1: "gateway",
        2: "worker",
        3: "cloud",
    }
    return labels.get(path_stage, "unknown")


def estimate_attack_success_probability(
    path_stage: int,
    attack_effect_success: bool,
    attack_success: bool,
    defense_active: bool,
    counters_stopped: bool,
) -> float:
    theta = 0.10 + (0.22 * path_stage)
    if attack_effect_success:
        theta += 0.16
    if attack_success:
        theta += 0.12
    if defense_active:
        theta -= 0.12
    if counters_stopped:
        theta -= 0.28
    return clamp(theta)


def compute_loss_rate(
    generated_total: float,
    gateway_received: float,
    gateway_dropped: float,
) -> float:
    if gateway_received + gateway_dropped > 0:
        return clamp(gateway_dropped / max(gateway_received + gateway_dropped, 1.0))
    if generated_total > 0:
        return clamp(max(generated_total - gateway_received, 0.0) / generated_total)
    return 0.0


def summarize_topology_status(status_data: Dict[str, Any]) -> Dict[str, Any]:
    switches = status_data.get("switches") if isinstance(status_data, dict) else []
    known_hosts = status_data.get("known_hosts") if isinstance(status_data, dict) else {}
    active_actions = status_data.get("active_actions") if isinstance(status_data, dict) else {}
    return {
        "switch_count": len(switches) if isinstance(switches, list) else 0,
        "connected_switch_count": sum(
            1 for switch in switches
            if isinstance(switch, dict) and switch.get("connected")
        ) if isinstance(switches, list) else 0,
        "known_host_count": len(known_hosts) if isinstance(known_hosts, dict) else 0,
        "active_action_count": len(active_actions) if isinstance(active_actions, dict) else 0,
        "active_actions": list(active_actions.values()) if isinstance(active_actions, dict) else [],
    }


def path_key(path: Iterable[Any]) -> str:
    return "->".join(str(item) for item in path)


def normalize_path(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def scenario_id_for_path(path: List[str], scenarios: Iterable[Dict[str, Any]]) -> str:
    for scenario in scenarios:
        if normalize_path(scenario.get("mulval_path")) == path:
            return str(scenario.get("scenario_id", ""))
    return ""


def scenario_for_path(path: List[str], scenarios: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    for scenario in scenarios:
        if normalize_path(scenario.get("mulval_path")) == path:
            return scenario
    return {}


def strategy_space_candidates_from_mulval(
    mulval_policy: Dict[str, Any],
    scenarios: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return path candidates generated by MulVAL plus scenario fallback seeds."""
    candidates: List[Dict[str, Any]] = []
    risk_scores = mulval_policy.get("path_risk_scores") or {}

    for path in mulval_policy.get("attack_paths") or []:
        normalized = normalize_path(path)
        if len(normalized) < 2:
            continue
        mapped_scenario = scenario_for_path(normalized, scenarios)
        scenario_id = (
            str(mapped_scenario.get("scenario_id", ""))
            or str(mulval_policy.get("scenario_id", ""))
        )
        candidates.append(
            {
                "source": "mulval_policy",
                "mulval_scenario_id": mulval_policy.get("scenario_id", ""),
                "scenario_id": scenario_id,
                "entry_node": normalized[0],
                "target_asset": normalized[-1],
                "path": normalized,
                "path_key": path_key(normalized),
                "risk_score": safe_float(risk_scores.get(path_key(normalized)), 0.50),
            }
        )

    for strategy in mulval_policy.get("attacker_strategy_space") or []:
        if not isinstance(strategy, dict):
            continue
        entry = str(strategy.get("entry_node", "") or "")
        target = str(strategy.get("target_asset", "") or "")
        pivot_sequence = normalize_path(strategy.get("pivot_sequence"))
        normalized = [entry] + pivot_sequence + ([target] if target else [])
        normalized = [node for node in normalized if node]
        if len(normalized) < 2:
            continue
        mapped_scenario = scenario_for_path(normalized, scenarios)
        scenario_id = (
            str(mapped_scenario.get("scenario_id", ""))
            or str(mulval_policy.get("scenario_id", ""))
        )
        candidate = {
            "source": "mulval_strategy_space",
            "mulval_scenario_id": mulval_policy.get("scenario_id", ""),
            "scenario_id": scenario_id,
            "entry_node": entry,
            "target_asset": target,
            "path": normalized,
            "path_key": path_key(normalized),
            "risk_score": safe_float(strategy.get("expected_damage_weight"), 0.50),
            "candidate_defender_actions": strategy.get("candidate_defender_actions", []),
        }
        if candidate["path_key"] not in {item["path_key"] for item in candidates}:
            candidates.append(candidate)

    # Scenario registry paths are fallback seeds. They keep the strategy layer
    # useful before every scenario has a generated MulVAL policy JSON.
    existing_keys = {candidate["path_key"] for candidate in candidates}
    for scenario in scenarios:
        normalized = normalize_path(scenario.get("mulval_path"))
        if len(normalized) < 2 or path_key(normalized) in existing_keys:
            continue
        candidates.append(
            {
                "source": "scenario_registry_seed",
                "mulval_scenario_id": "",
                "scenario_id": str(scenario.get("scenario_id", "")),
                "entry_node": str(scenario.get("entry_node", "") or normalized[0]),
                "target_asset": str(scenario.get("target_asset", "") or normalized[-1]),
                "path": normalized,
                "path_key": path_key(normalized),
                "risk_score": 0.50,
                "candidate_defender_actions": scenario.get("candidate_defender_actions", []),
            }
        )

    return candidates


def plausible_candidates_for_scenario(
    candidates: List[Dict[str, Any]],
    scenario_id: str,
    path: List[str],
) -> List[Dict[str, Any]]:
    path_id = path_key(path)
    result = [
        candidate for candidate in candidates
        if candidate.get("scenario_id") == scenario_id
        or candidate.get("path_key") == path_id
    ]
    return result


def build_state(
    core_url: Optional[str] = DEFAULT_CORE_URL,
    mtd_metrics_url: Optional[str] = DEFAULT_MTD_METRICS_URL,
    mtd_status_url: Optional[str] = DEFAULT_MTD_STATUS_URL,
    scenario_id: Optional[str] = None,
    scenario_registry_path: Path = DEFAULT_SCENARIO_REGISTRY,
    mulval_policy_path: Optional[Path] = DEFAULT_MULVAL_POLICY,
    core_data: Optional[Dict[str, Any]] = None,
    mtd_metrics_text: Optional[str] = None,
    mtd_status_data: Optional[Dict[str, Any]] = None,
    timeout: float = 2.0,
    constraints: Optional[Dict[str, Any]] = None,
    core_docker_fallback: bool = DEFAULT_CORE_DOCKER_FALLBACK,
    cloud_metrics_container: str = DEFAULT_CLOUD_METRICS_CONTAINER,
) -> Dict[str, Any]:
    errors: List[str] = []
    notes: List[str] = []
    scenarios = load_json_file(scenario_registry_path, default=[])
    if not isinstance(scenarios, list):
        scenarios = []
        errors.append(f"scenario registry is not a list: {scenario_registry_path}")

    mulval_policy = load_json_file(mulval_policy_path, default={}) if mulval_policy_path else {}
    if not isinstance(mulval_policy, dict):
        mulval_policy = {}
    mulval_candidates = strategy_space_candidates_from_mulval(mulval_policy, scenarios)

    if core_data is None:
        before_core_errors = len(errors)
        core_data = try_fetch_json(core_url, timeout, errors)
        direct_core_errors = errors[before_core_errors:]
        if not core_data and core_docker_fallback and core_url:
            fallback_core_data = fetch_json_from_container(
                cloud_metrics_container,
                "/core",
                timeout,
                errors,
            )
            if fallback_core_data:
                core_data = fallback_core_data
                del errors[before_core_errors:]
                if direct_core_errors:
                    notes.append(
                        "direct /core fetch failed; used Docker fallback "
                        f"{cloud_metrics_container}:8000/core"
                    )
    if mtd_metrics_text is None:
        mtd_metrics_text = try_fetch_text(mtd_metrics_url, timeout, errors)
    if mtd_status_data is None:
        mtd_status_data = try_fetch_json(mtd_status_url, timeout, errors)

    controller_data = parse_prometheus_text(mtd_metrics_text or "")
    selected_scenario_id = choose_scenario_id(core_data, scenarios, mulval_policy, scenario_id)
    scenario = scenario_by_id(scenarios, selected_scenario_id)
    path = list(scenario.get("mulval_path") or [])
    if not path and mulval_policy.get("attack_paths"):
        path = list(mulval_policy["attack_paths"][0])
    plausible_candidates = plausible_candidates_for_scenario(
        mulval_candidates,
        selected_scenario_id,
        path,
    )
    current_path_risk = max(
        [safe_float(candidate.get("risk_score"), 0.0) for candidate in plausible_candidates]
        or [0.0]
    )

    entry_node = str(scenario.get("entry_node") or (path[0] if path else ""))
    target_asset = str(scenario.get("target_asset") or (path[-1] if path else ""))
    gateway_node = path[1] if len(path) > 1 else ""
    worker_node = path[2] if len(path) > 2 else ""

    attack_rows = active_rows_for_scenario(core_data, "attack_events", selected_scenario_id)
    defense_rows = active_rows_for_scenario(core_data, "defense_events", selected_scenario_id)
    attack_metrics = metric_map(attack_rows)
    defense_metrics = metric_map(defense_rows)
    has_attack_context = bool(attack_rows)

    message_rows = rows(core_data, "message_loss_counters")
    throughput_rows = rows(core_data, "throughput")
    resource_rows = rows(core_data, "resource_use")
    sensor_edge_rows = rows(core_data, "sensor_to_edge_latency_ms")
    edge_cloud_rows = rows(core_data, "edge_to_cloud_latency_ms")

    generated_total = metric_value(
        message_rows,
        source=entry_node,
        metric="generated_total",
        role="sensor",
        default=0.0,
    ) or 0.0
    gateway_received = metric_value(
        message_rows,
        source=gateway_node,
        metric=f"sensors.{entry_node}.received",
        role="edge_gateway",
        default=0.0,
    ) or 0.0
    gateway_dropped = metric_value(
        message_rows,
        source=gateway_node,
        metric=f"sensors.{entry_node}.dropped",
        role="edge_gateway",
        default=0.0,
    ) or 0.0
    gateway_forwarded = metric_value(
        message_rows,
        source=gateway_node,
        metric=f"sensors.{entry_node}.forwarded",
        role="edge_gateway",
        default=0.0,
    ) or 0.0
    worker_requests = metric_value(
        message_rows,
        source=worker_node,
        metric="requests_total",
        role="edge_worker",
        default=0.0,
    ) or 0.0
    cloud_storage_confirmations = metric_value(
        message_rows,
        source="cloud_db",
        metric="storage_confirmations_total",
        role="cloud_db",
        default=0.0,
    ) or 0.0

    gateway_seen = bool_metric(attack_metrics.get("gateway_seen")) or (
        has_attack_context and gateway_received > 0
    )
    worker_seen = (
        bool_metric(attack_metrics.get("worker_seen"))
        or bool_metric(attack_metrics.get("worker_requests_increase"))
        or (has_attack_context and worker_requests > 0)
    )
    cloud_seen = (
        bool_metric(attack_metrics.get("cloud_seen"))
        or bool_metric(attack_metrics.get("cloud_summary_rate_changes"))
        or (has_attack_context and cloud_storage_confirmations > 0)
    )
    attack_effect_success = bool_metric(attack_metrics.get("attack_effect_success"))
    attack_success = bool_metric(attack_metrics.get("success"))
    attack_active = bool_metric(attack_metrics.get("attack_active")) or has_attack_context

    path_stage = 3 if cloud_seen else 2 if worker_seen else 1 if gateway_seen else 0

    topology_summary = summarize_topology_status(mtd_status_data)
    controller_metrics = controller_data["metrics"]
    controller_active_actions = safe_int(
        controller_metrics.get("ryu_controller_active_policy_actions"),
        default=safe_int(topology_summary.get("active_action_count")),
    )
    controller_apply_ms = safe_float(
        controller_metrics.get("ryu_controller_last_action_duration_ms")
    )
    flow_rules_installed = safe_int(
        controller_metrics.get("ryu_controller_flow_rules_installed_total")
    )
    flow_delete_commands = safe_int(
        controller_metrics.get("ryu_controller_flow_delete_commands_total")
    )
    meters_added = safe_int(controller_metrics.get("ryu_controller_meters_added_total"))
    controller_reachable = bool(mtd_metrics_text or mtd_status_data)

    reported_defense_success = bool_metric(defense_metrics.get("defense_success")) or bool_metric(
        defense_metrics.get("success")
    )
    reported_drop_rules_active = bool_metric(defense_metrics.get("drop_rules_active"))
    reported_counters_stopped = bool_metric(defense_metrics.get("counters_stopped"))
    reported_defense_active = (
        reported_defense_success
        or reported_drop_rules_active
        or reported_counters_stopped
    )
    controller_has_active_defense = controller_active_actions > 0
    stale_defense_metrics = (
        controller_reachable
        and reported_defense_active
        and not controller_has_active_defense
    )
    if stale_defense_metrics:
        notes.append(
            "defense metrics report success/active signals but Ryu has zero "
            "active policy actions; treating defense metrics as stale"
        )
    defense_success = reported_defense_success and not stale_defense_metrics
    drop_rules_active = reported_drop_rules_active and not stale_defense_metrics
    counters_stopped = reported_counters_stopped and not stale_defense_metrics
    defense_active = controller_has_active_defense or (
        reported_defense_active and not stale_defense_metrics
    )

    sensor_to_edge_latency_ms = avg_metrics(
        sensor_edge_rows,
        lambda row: str(row.get("metric", "")).endswith("last_ingestion_latency_ms")
        and "unmapped" not in str(row.get("metric", "")),
    )
    edge_to_cloud_latency_ms = avg_metrics(edge_cloud_rows, lambda _row: True)
    loss_rate = compute_loss_rate(generated_total, gateway_received, gateway_dropped)

    total_cpu_seconds = sum_metrics(resource_rows, lambda row: "cpu_seconds" in str(row.get("metric", "")))
    total_memory_kb = sum_metrics(resource_rows, lambda row: "memory_kb" in str(row.get("metric", "")))
    throughput_bytes_per_second = sum_metrics(
        throughput_rows,
        lambda row: str(row.get("metric", "")).endswith("bytes_per_second"),
    )

    theta = estimate_attack_success_probability(
        path_stage,
        attack_effect_success,
        attack_success,
        defense_active,
        counters_stopped,
    )

    operational_constraints = {
        "max_attack_cost": 1.0,
        "max_defense_cost": 1.0,
        "allow_disruptive_defense": True,
        "strict_preconditions": False,
    }
    if constraints:
        operational_constraints.update(constraints)

    return {
        "schema_version": "llm-mtd-game-state-v1",
        "built_at": utc_now_iso(),
        "scenario_id": selected_scenario_id,
        "scenario_known": bool(scenario),
        "entry_node": entry_node,
        "target_asset": target_asset,
        "current_path": path,
        "path_stage": path_stage,
        "path_stage_label": path_stage_label(path_stage),
        "mulval": {
            "policy_path": str(mulval_policy_path) if mulval_policy_path else "",
            "policy_scenario_id": mulval_policy.get("scenario_id", ""),
            "path_candidates": mulval_candidates,
            "plausible_candidates": plausible_candidates,
            "plausible_paths": [candidate["path"] for candidate in plausible_candidates],
            "current_path_risk": current_path_risk,
        },
        "attack_active": attack_active,
        "attack_success": attack_success,
        "attack_effect_success": attack_effect_success,
        "defense_active": defense_active,
        "defense_success": defense_success,
        "defense_metrics_stale": stale_defense_metrics,
        "drop_rules_active": drop_rules_active,
        "counters_stopped": counters_stopped,
        "controller_reachable": controller_reachable,
        "observability": {
            "defender": "full_system_state",
            "attacker": "partial_local_and_inferred_state",
        },
        "attacker_observation": {
            "foothold": entry_node,
            "observed_path_stage": path_stage,
            "observed_blocking": defense_active or drop_rules_active or counters_stopped,
            "estimated_success_probability": theta,
            "estimated_containment": clamp(
                (0.35 if drop_rules_active else 0.0)
                + (0.45 if counters_stopped else 0.0)
                + (0.10 if controller_active_actions else 0.0)
            ),
            "estimated_risk": clamp(
                0.12
                + (0.10 if attack_effect_success else 0.0)
                + (0.20 if defense_active else 0.0)
                + (0.25 if drop_rules_active else 0.0)
            ),
        },
        "defender_observation": {
            "gateway_seen": gateway_seen,
            "worker_seen": worker_seen,
            "cloud_seen": cloud_seen,
            "attack_metrics": attack_metrics,
            "defense_metrics": defense_metrics,
            "topology": topology_summary,
        },
        "qos": {
            "sensor_to_edge_latency_ms": sensor_to_edge_latency_ms,
            "edge_to_cloud_latency_ms": edge_to_cloud_latency_ms,
            "loss_rate": loss_rate,
            "throughput_bytes_per_second": throughput_bytes_per_second,
        },
        "workload": {
            "generated_total": generated_total,
            "gateway_received": gateway_received,
            "gateway_forwarded": gateway_forwarded,
            "gateway_dropped": gateway_dropped,
            "worker_requests": worker_requests,
            "cloud_storage_confirmations": cloud_storage_confirmations,
        },
        "overhead": {
            "controller_apply_ms": controller_apply_ms,
            "flow_rules_installed": flow_rules_installed,
            "flow_delete_commands": flow_delete_commands,
            "meters_added": meters_added,
            "controller_active_actions": controller_active_actions,
            "total_cpu_seconds": total_cpu_seconds,
            "total_memory_kb": total_memory_kb,
        },
        "controller": {
            "active_actions": topology_summary.get("active_actions", []),
        },
        "operational_constraints": operational_constraints,
        "source_errors": errors,
        "source_notes": notes,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the current LLM_MTD_emo strategy state.")
    parser.add_argument("--core-url", default=DEFAULT_CORE_URL)
    parser.add_argument("--mtd-metrics-url", default=DEFAULT_MTD_METRICS_URL)
    parser.add_argument("--mtd-status-url", default=DEFAULT_MTD_STATUS_URL)
    parser.add_argument("--cloud-metrics-container", default=DEFAULT_CLOUD_METRICS_CONTAINER)
    parser.add_argument("--scenario-id", default="")
    parser.add_argument("--scenario-registry", type=Path, default=DEFAULT_SCENARIO_REGISTRY)
    parser.add_argument("--mulval-policy", type=Path, default=DEFAULT_MULVAL_POLICY)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not fetch live endpoints; build from local scenario files only.",
    )
    parser.add_argument(
        "--no-core-docker-fallback",
        action="store_true",
        help="Disable Docker fallback through mn.cloud_metrics when /core is unreachable from the host.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    state = build_state(
        core_url=None if args.offline else args.core_url,
        mtd_metrics_url=None if args.offline else args.mtd_metrics_url,
        mtd_status_url=None if args.offline else args.mtd_status_url,
        scenario_id=args.scenario_id or None,
        scenario_registry_path=args.scenario_registry,
        mulval_policy_path=args.mulval_policy,
        timeout=args.timeout_seconds,
        core_docker_fallback=not args.no_core_docker_fallback,
        cloud_metrics_container=args.cloud_metrics_container,
    )
    json.dump(state, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
