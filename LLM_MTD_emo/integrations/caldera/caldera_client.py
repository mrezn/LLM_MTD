#!/usr/bin/env python3
"""Record observe-only Caldera trial results for the LLM_MTD_emo lab.

This helper intentionally avoids the Caldera REST API for the first lab phase.
Use the Caldera GUI/manual mode to run the operation, then use this script to
turn the observed deltas into one compact JSON record for logger/metrics/policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_REGISTRY = REPO_ROOT / "integrations" / "attack_scenarios.json"
DEFAULT_RESULTS_DIR = REPO_ROOT / "integrations" / "caldera" / "results"
DEFAULT_SCENARIO_ID = "sen4_edge2_clouddb"
PATH_ADVERSARY_MAP = {
    "sensor_to_edge_http_abuse": "sensor_to_edge",
    "edge_gateway_service_abuse": "edge_to_cloud",
    "dual_homed_sensor_path_abuse": "dual_homed_sensor_path",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "y", "ok", "success"):
        return True
    if normalized in ("0", "false", "no", "n", "fail", "failed"):
        return False
    raise argparse.ArgumentTypeError(f"expected a boolean value, got {value!r}")


def read_json_file(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_scenario(registry_path: Path, scenario_id: str) -> dict:
    scenarios = read_json_file(registry_path)
    for scenario in scenarios:
        if scenario.get("scenario_id") == scenario_id:
            return scenario
    raise SystemExit(f"scenario_id {scenario_id!r} was not found in {registry_path}")


def post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "url": url,
                "body": response_body,
                "error": "",
            }
    except urllib.error.HTTPError as error:
        return {
            "ok": False,
            "status": error.code,
            "url": url,
            "body": error.read().decode("utf-8", errors="replace"),
            "error": str(error),
        }
    except Exception as error:  # pragma: no cover - depends on lab networking
        return {"ok": False, "status": 0, "url": url, "body": "", "error": str(error)}


def endpoint_url(url: str, fallback_path: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url
    return url.rstrip("/") + fallback_path


def optional_int(value: Optional[int]) -> int:
    return 0 if value is None else int(value)


def build_result_record(args: argparse.Namespace, scenario: dict) -> dict:
    gateway_delta = optional_int(args.gateway_received_delta)
    worker_delta = optional_int(args.worker_request_delta)
    cloud_delta = optional_int(args.cloud_summary_delta)
    queue_spike = bool(args.gateway_queue_spike) if args.gateway_queue_spike is not None else False

    signals = {
        "gateway_received_delta": gateway_delta,
        "worker_request_delta": worker_delta,
        "cloud_summary_delta": cloud_delta,
        "gateway_queue_spike": queue_spike,
    }
    if args.edge_gateway_metric_delta is not None:
        signals["edge_gateway_metric_delta"] = args.edge_gateway_metric_delta
    if args.worker_error_delta is not None:
        signals["worker_error_delta"] = args.worker_error_delta
    if args.cloud_error_delta is not None:
        signals["cloud_error_delta"] = args.cloud_error_delta

    live_attack_type = scenario.get("live_attack_type", "")
    adversary_id = args.adversary_id or PATH_ADVERSARY_MAP.get(live_attack_type, live_attack_type)

    return {
        "event_type": "attack_result",
        "tool": "caldera",
        "operation_id": args.operation_id,
        "scenario_id": scenario["scenario_id"],
        "entry_node": scenario["entry_node"],
        "attempted_path": scenario.get("mulval_path", []),
        "target_asset": scenario.get("target_asset", ""),
        "live_attack_type": live_attack_type,
        "adversary_id": adversary_id,
        "success": args.success,
        "gateway_seen": gateway_delta > 0,
        "worker_seen": worker_delta > 0,
        "worker_requests_increase": worker_delta > 0,
        "cloud_seen": cloud_delta > 0,
        "cloud_summary_rate_changes": cloud_delta > 0,
        "gateway_queue_spike": queue_spike,
        "attack_effect_success": args.attack_effect_success,
        "signals": signals,
        "timestamp": now_iso(),
    }


def build_defense_record(args: argparse.Namespace, scenario: dict) -> Optional[dict]:
    if not args.defense_action and args.defense_success is None:
        return None

    target = args.defense_target or scenario.get("entry_node", "unknown")
    signals = {}
    if args.controller_action_duration_ms is not None:
        signals["controller_action_duration_ms"] = args.controller_action_duration_ms
    if args.ovs_drop_counter_delta is not None:
        signals["ovs_drop_counter_delta"] = args.ovs_drop_counter_delta
    if args.drop_rules_active is not None:
        signals["drop_rules_active"] = args.drop_rules_active
    if args.counters_stopped is not None:
        signals["counters_stopped"] = args.counters_stopped

    return {
        "event_type": "defense_result",
        "tool": "ryu_mtd",
        "operation_id": args.operation_id,
        "scenario_id": scenario["scenario_id"],
        "defense_action": args.defense_action or "unknown",
        "target": target,
        "success": args.defense_success if args.defense_success is not None else False,
        "defense_success": args.defense_success if args.defense_success is not None else False,
        "signals": signals,
        "timestamp": now_iso(),
    }


def default_output_path(operation_id: str) -> Path:
    return DEFAULT_RESULTS_DIR / f"{operation_id}.json"


def write_result(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize a manual Caldera operation into policy-friendly JSON."
    )
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--scenario-id", default=DEFAULT_SCENARIO_ID)
    parser.add_argument("--scenario-registry", type=Path, default=DEFAULT_SCENARIO_REGISTRY)
    parser.add_argument("--adversary-id", default="")
    parser.add_argument("--success", required=True, type=parse_bool)
    parser.add_argument("--attack-effect-success", type=parse_bool, default=False)
    parser.add_argument("--gateway-received-delta", type=int)
    parser.add_argument("--worker-request-delta", type=int)
    parser.add_argument("--cloud-summary-delta", type=int)
    parser.add_argument("--gateway-queue-spike", type=parse_bool)
    parser.add_argument("--edge-gateway-metric-delta", type=int)
    parser.add_argument("--worker-error-delta", type=int)
    parser.add_argument("--cloud-error-delta", type=int)
    parser.add_argument("--defense-action", default="")
    parser.add_argument("--defense-target", default="")
    parser.add_argument("--defense-success", type=parse_bool)
    parser.add_argument("--controller-action-duration-ms", type=float)
    parser.add_argument("--ovs-drop-counter-delta", type=int)
    parser.add_argument("--drop-rules-active", type=parse_bool)
    parser.add_argument("--counters-stopped", type=parse_bool)
    parser.add_argument("--mulval-policy-json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--logger-url", default=os.environ.get("CLOUD_LOGGER_URL", ""))
    parser.add_argument("--metrics-url", default=os.environ.get("CLOUD_METRICS_URL", ""))
    parser.add_argument("--policy-url", default=os.environ.get("CLOUD_POLICY_URL", ""))
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    scenario = load_scenario(args.scenario_registry, args.scenario_id)
    result_record = build_result_record(args, scenario)
    defense_record = build_defense_record(args, scenario)

    output_path = args.output or default_output_path(args.operation_id)
    stored_payload = {"caldera_result": result_record}
    if defense_record:
        stored_payload["defense_result"] = defense_record
    write_result(output_path, stored_payload)

    post_results = []
    if args.logger_url:
        logger_url = endpoint_url(args.logger_url, "/attack/event")
        post_results.append(post_json(logger_url, result_record, args.timeout_seconds))
        if defense_record:
            post_results.append(post_json(logger_url, defense_record, args.timeout_seconds))
    if args.metrics_url:
        metrics_url = endpoint_url(args.metrics_url, "/attack/event")
        post_results.append(post_json(metrics_url, result_record, args.timeout_seconds))
        if defense_record:
            post_results.append(post_json(metrics_url, defense_record, args.timeout_seconds))
    if args.policy_url:
        policy_payload = {"caldera_result": result_record, "observe_only": True}
        if defense_record:
            policy_payload["defense_result"] = defense_record
        if args.mulval_policy_json:
            policy_payload["mulval_policy"] = read_json_file(args.mulval_policy_json)
        policy_url = endpoint_url(args.policy_url, "/context")
        post_results.append(post_json(policy_url, policy_payload, args.timeout_seconds))

    summary = {
        "status": "recorded",
        "output": str(output_path),
        "scenario_id": args.scenario_id,
        "post_results": post_results,
    }
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
