#!/usr/bin/env python3
"""Run one strategy-selection stage and optionally dispatch actions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from game_model import evolutionary_step
from policy_selector import compact_selection, select_pair
from state_builder import (
    DEFAULT_CLOUD_METRICS_CONTAINER,
    DEFAULT_CORE_URL,
    DEFAULT_CORE_DOCKER_FALLBACK,
    DEFAULT_MTD_METRICS_URL,
    DEFAULT_MTD_STATUS_URL,
    DEFAULT_MULVAL_POLICY,
    DEFAULT_SCENARIO_REGISTRY,
    build_state,
)
from stage_transition import (
    DEFAULT_DECISION_TRACE_LOG,
    DEFAULT_STAGE_LOG,
    append_decision_trace,
    append_transition,
    build_decision_trace_record,
    build_transition_record,
)
from strategy_manager import DEFAULT_STRATEGY_SPACE, StrategyManager


STRATEGY_DIR = Path(__file__).resolve().parent
DEFAULT_POPULATION_STATE = STRATEGY_DIR / "population_state.json"
DEFAULT_RYU_ACTION_URL = "http://127.0.0.1:8080/mtd/action"
DEFAULT_ATTACKER_DISPATCH_URL = os.environ.get("STRATEGY_ATTACKER_DISPATCH_URL", "")
DEFAULT_CLOUD_LOGGER_URL = os.environ.get("STRATEGY_CLOUD_LOGGER_URL", "")
DEFAULT_CLOUD_POLICY_CONTAINER = os.environ.get(
    "STRATEGY_CLOUD_POLICY_CONTAINER",
    "mn.cloud_policy",
)
DEFAULT_CLOUD_POLICY_DOCKER_FALLBACK = os.environ.get(
    "STRATEGY_CLOUD_POLICY_DOCKER_FALLBACK",
    "1",
).strip().lower() not in ("0", "false", "no")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(safe_float(value, float(default))))
    except (TypeError, ValueError):
        return default


def response_json(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    try:
        parsed = json.loads(result.get("body") or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def post_json(url: str, payload: Dict[str, Any], timeout: float = 2.0) -> Dict[str, Any]:
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
    except Exception as error:  # pragma: no cover - live lab dependent
        return {"ok": False, "status": 0, "url": url, "body": "", "error": str(error)}


def local_container_url(url: str, fallback_path: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path if parsed.path and parsed.path != "/" else fallback_path
    query = f"?{parsed.query}" if parsed.query else ""
    return f"http://127.0.0.1:8000{path}{query}"


def post_json_from_container(
    container_name: str,
    url: str,
    payload: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    if not container_name:
        return {
            "ok": False,
            "status": 0,
            "url": url,
            "body": "",
            "error": "missing Docker container name",
        }

    python_code = (
        "import json, sys, urllib.error, urllib.request\n"
        "url = sys.argv[1]\n"
        "timeout = float(sys.argv[2])\n"
        "body = sys.stdin.buffer.read()\n"
        "request = urllib.request.Request(\n"
        "    url,\n"
        "    data=body,\n"
        "    headers={'Content-Type': 'application/json'},\n"
        "    method='POST',\n"
        ")\n"
        "try:\n"
        "    with urllib.request.urlopen(request, timeout=timeout) as response:\n"
        "        response_body = response.read().decode('utf-8', errors='replace')\n"
        "        print(json.dumps({\n"
        "            'ok': 200 <= response.status < 300,\n"
        "            'status': response.status,\n"
        "            'body': response_body,\n"
        "            'error': '',\n"
        "        }))\n"
        "except urllib.error.HTTPError as error:\n"
        "    print(json.dumps({\n"
        "        'ok': False,\n"
        "        'status': error.code,\n"
        "        'body': error.read().decode('utf-8', errors='replace'),\n"
        "        'error': str(error),\n"
        "    }))\n"
        "except Exception as error:\n"
        "    print(json.dumps({'ok': False, 'status': 0, 'body': '', 'error': str(error)}))\n"
    )
    body = json.dumps(payload).encode("utf-8")
    commands = [
        ["docker", "exec", "-i", container_name, "python3", "-c", python_code, url, str(timeout)],
        ["docker", "exec", "-i", container_name, "python", "-c", python_code, url, str(timeout)],
    ]

    attempt_errors = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                input=body,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(timeout + 4.0, 6.0),
            )
        except Exception as error:  # pragma: no cover - deployment dependent
            attempt_errors.append(f"{command[4]} failed to start: {error}")
            continue

        if result.returncode == 0:
            try:
                parsed = json.loads(result.stdout.decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError as error:
                attempt_errors.append(f"{command[4]} returned invalid json: {error}")
                continue
            return {
                **parsed,
                "url": url,
                "transport": "docker_exec",
                "container": container_name,
            }

        detail = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or f"exit code {result.returncode}"
        )
        attempt_errors.append(f"{command[4]}: {detail}")

    return {
        "ok": False,
        "status": 0,
        "url": url,
        "body": "",
        "transport": "docker_exec",
        "container": container_name,
        "error": " | ".join(attempt_errors),
    }


def post_json_with_container_fallback(
    url: str,
    payload: Dict[str, Any],
    timeout: float,
    docker_container: str,
    docker_fallback: bool,
    fallback_path: str,
) -> Dict[str, Any]:
    direct_result = post_json(url, payload, timeout=timeout)
    if direct_result.get("ok") or not docker_fallback or direct_result.get("status") != 0:
        return direct_result

    container_url = local_container_url(url, fallback_path)
    fallback_result = post_json_from_container(
        docker_container,
        container_url,
        payload,
        timeout,
    )
    if fallback_result.get("ok"):
        return {
            **fallback_result,
            "fallback_used": True,
            "direct_result": direct_result,
        }
    return {
        **direct_result,
        "docker_fallback": fallback_result,
    }


def parse_response_json(result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(result.get("body") or "{}")
    except json.JSONDecodeError:
        return {}


def endpoint_url(url: str, fallback_path: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.path in ("/context", "/decide"):
        return urllib.parse.urlunparse(parsed._replace(path=fallback_path))
    if parsed.path and parsed.path != "/":
        return url
    return url.rstrip("/") + fallback_path


def load_population(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_population(path: Path, game_result: Dict[str, Any], stage_result: Dict[str, Any]) -> None:
    payload = {
        "attacker": game_result.get("attacker_population", {}),
        "defender": game_result.get("defender_population", {}),
        "last_stage": {
            "scenario_id": stage_result.get("state", {}).get("scenario_id"),
            "path_stage": stage_result.get("state", {}).get("path_stage"),
            "selected_attacker": (
                stage_result.get("selection", {}).get("attacker") or {}
            ).get("id"),
            "selected_defender": (
                stage_result.get("selection", {}).get("defender") or {}
            ).get("id"),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def state_workload_is_zero(state: Dict[str, Any]) -> bool:
    workload = state.get("workload")
    if not isinstance(workload, dict) or not workload:
        return True
    return all(float(value or 0.0) == 0.0 for value in workload.values())


def state_has_empty_attack_defense_metrics(state: Dict[str, Any]) -> bool:
    defender_observation = state.get("defender_observation") or {}
    return not defender_observation.get("attack_metrics") and not defender_observation.get(
        "defense_metrics"
    )


def state_has_ingestion_errors(state: Dict[str, Any]) -> bool:
    errors = state.get("source_errors") or []
    return any(str(error).strip() for error in errors)


def should_skip_persistence_for_state(state: Dict[str, Any]) -> bool:
    """Avoid teaching the game from failed live-ingestion placeholder states."""
    return (
        int(state.get("path_stage") or 0) == 0
        and state_has_empty_attack_defense_metrics(state)
        and state_workload_is_zero(state)
        and state_has_ingestion_errors(state)
    )


def action_payload_from_defender(selection: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not selection:
        return None
    strategy = selection["strategy"]
    payload = dict(strategy.get("action_payload") or {})
    if not payload:
        payload["action"] = strategy.get("action")
        if strategy.get("target"):
            payload["target"] = strategy.get("target")
    payload.setdefault("source_strategy_id", selection["id"])
    payload.setdefault("source", "strategy_runtime")
    return payload


def build_attacker_execution_plan(
    selection: Optional[Dict[str, Any]],
    state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not selection:
        return None
    strategy = selection["strategy"]
    executor = dict(strategy.get("executor") or {})
    env = dict(executor.get("env") or {})
    env.setdefault("SCENARIO_ID", strategy.get("scenario_id") or state.get("scenario_id"))
    env.setdefault("ENTRY_NODE", strategy.get("entry_node") or state.get("entry_node"))
    if str(strategy.get("entry_node", "")).startswith("sen"):
        env.setdefault("ATTACK_SENSOR_ID", strategy.get("entry_node"))

    target_hosts: List[str] = []
    for candidate in (
        env.get("ENTRY_NODE"),
        env.get("ATTACK_SENSOR_ID"),
        strategy.get("entry_node"),
    ):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in target_hosts:
            target_hosts.append(normalized)
    if not target_hosts:
        path = strategy.get("path") or []
        if path:
            first_hop = str(path[0] or "").strip()
            if first_hop:
                target_hosts.append(first_hop)

    return {
        "strategy_id": selection["id"],
        "strategy_name": strategy.get("name"),
        "scenario_id": strategy.get("scenario_id"),
        "live_attack_type": strategy.get("live_attack_type"),
        "executor": executor,
        "caldera_adversary": executor.get("name"),
        "caldera_adversary_file": executor.get("adversary_file"),
        "caldera_adversary_yaml_id": executor.get("adversary_yaml_id"),
        "caldera_env": env,
        "caldera_target_hosts": target_hosts,
        "path": strategy.get("path"),
        "mulval": {
            "current_path_risk": (state.get("mulval") or {}).get("current_path_risk"),
            "plausible_paths": (state.get("mulval") or {}).get("plausible_paths", []),
        },
        "expected_effects": strategy.get("expected_effects", []),
        "note": (
            "Dispatch this plan to Caldera manually or through --attacker-dispatch-url. "
            "The first lab workflow still keeps Caldera operation launch outside this repo."
        ),
    }


def merge_context(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(fallback or {})
    for key, value in (primary or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        merged[key] = value
    return merged


def attack_context_from_execution(
    state: Dict[str, Any],
    attacker_execution: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(attacker_execution, dict):
        return {}

    plan = attacker_execution.get("plan")
    if not isinstance(plan, dict) or not plan:
        return {}

    post_body = response_json(attacker_execution.get("post_result"))
    status = str(attacker_execution.get("status", "")).strip().lower()
    operation_id = (
        str(post_body.get("operation_id") or post_body.get("id") or "").strip()
        or f"strategy-{state.get('scenario_id', 'unknown')}"
    )
    path = plan.get("path") or state.get("current_path", [])
    entry_node = (
        (plan.get("caldera_env") or {}).get("ENTRY_NODE")
        or state.get("entry_node")
        or (path[0] if path else "")
    )
    if not path and not entry_node:
        return {}

    return {
        "event_type": "attack_start" if status == "dispatched" else "attack_selected",
        "tool": "strategy_runtime",
        "operation_id": operation_id,
        "scenario_id": plan.get("scenario_id") or state.get("scenario_id"),
        "entry_node": entry_node,
        "attempted_path": path,
        "target_asset": state.get("target_asset"),
        "live_attack_type": plan.get("live_attack_type", ""),
        "adversary_id": plan.get("caldera_adversary") or plan.get("caldera_adversary_yaml_id") or "",
        "success": None,
        "gateway_seen": False,
        "worker_seen": False,
        "cloud_seen": False,
        "attack_effect_success": False,
        "signals": {
            "dispatch_status": attacker_execution.get("status", ""),
            "dispatch_http_status": (attacker_execution.get("post_result") or {}).get("status", 0),
        },
        "timestamp": utc_now_iso(),
    }


def cloud_policy_context_payload(
    state: Dict[str, Any],
    selection: Dict[str, Any],
    attacker_execution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "strategy_layer": {
            "state": {
                "scenario_id": state.get("scenario_id"),
                "path_stage": state.get("path_stage"),
                "attack_active": state.get("attack_active"),
                "defense_active": state.get("defense_active"),
                "current_path": state.get("current_path", []),
                "mulval": state.get("mulval", {}),
            },
            "selection": {
                "attacker": compact_selection(selection.get("attacker")),
                "defender": compact_selection(selection.get("defender")),
            },
        },
        "selected_attacker_strategy": compact_selection(selection.get("attacker")),
        "selected_defender_strategy": compact_selection(selection.get("defender")),
    }

    attack_context = merge_context(
        attack_context_from_state(state),
        attack_context_from_execution(state, attacker_execution),
    )
    if attack_context:
        payload["caldera_result"] = attack_context

    defense_context = defense_context_from_state(state)
    if defense_context:
        payload["defense_result"] = defense_context

    return payload


def attack_context_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = state.get("defender_observation") or {}
    attack_metrics = observation.get("attack_metrics") or {}
    if not attack_metrics and not state.get("attack_active") and not state.get("attack_success"):
        return {}

    return {
        "event_type": "attack_result",
        "tool": "strategy_state",
        "operation_id": f"state-{state.get('scenario_id', 'unknown')}",
        "scenario_id": state.get("scenario_id"),
        "entry_node": state.get("entry_node"),
        "attempted_path": state.get("current_path", []),
        "target_asset": state.get("target_asset"),
        "success": bool(state.get("attack_success")),
        "gateway_seen": bool(observation.get("gateway_seen")),
        "worker_seen": bool(observation.get("worker_seen")),
        "cloud_seen": bool(observation.get("cloud_seen")),
        "attack_effect_success": bool(state.get("attack_effect_success")),
        "signals": attack_metrics,
        "timestamp": state.get("built_at") or utc_now_iso(),
    }


def defense_context_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    observation = state.get("defender_observation") or {}
    defense_metrics = observation.get("defense_metrics") or {}
    active_actions = (state.get("controller") or {}).get("active_actions") or []
    active_action = active_actions[0] if active_actions and isinstance(active_actions[0], dict) else {}
    if not defense_metrics and not active_action and not state.get("defense_active"):
        return {}

    action = active_action.get("action") or "unknown"
    target = active_action.get("target") or state.get("entry_node") or "unknown"
    return {
        "event_type": "defense_result",
        "tool": "strategy_state",
        "operation_id": f"state-{state.get('scenario_id', 'unknown')}",
        "scenario_id": state.get("scenario_id"),
        "defense_action": action,
        "target": target,
        "success": bool(state.get("defense_success")),
        "defense_success": bool(state.get("defense_success")),
        "signals": {
            **defense_metrics,
            "active_policy_actions": (state.get("overhead") or {}).get(
                "controller_active_actions",
                0,
            ),
            "flow_rules_installed": (state.get("overhead") or {}).get(
                "flow_rules_installed",
                0,
            ),
            "meters_added": (state.get("overhead") or {}).get("meters_added", 0),
            "drop_rules_active": bool(state.get("drop_rules_active")),
            "counters_stopped": bool(state.get("counters_stopped")),
        },
        "timestamp": state.get("built_at") or utc_now_iso(),
    }


def post_cloud_policy_context(
    cloud_policy_url: str,
    state: Dict[str, Any],
    selection: Dict[str, Any],
    attacker_execution: Optional[Dict[str, Any]],
    timeout: float,
    docker_container: str = DEFAULT_CLOUD_POLICY_CONTAINER,
    docker_fallback: bool = DEFAULT_CLOUD_POLICY_DOCKER_FALLBACK,
) -> Dict[str, Any]:
    context_url = endpoint_url(cloud_policy_url, "/context")
    payload = cloud_policy_context_payload(state, selection, attacker_execution=attacker_execution)
    return post_json_with_container_fallback(
        context_url,
        payload,
        timeout=timeout,
        docker_container=docker_container,
        docker_fallback=docker_fallback,
        fallback_path="/context",
    )


def ask_cloud_policy_decision(
    cloud_policy_url: str,
    state: Dict[str, Any],
    selection: Dict[str, Any],
    attacker_execution: Optional[Dict[str, Any]],
    timeout: float,
    docker_container: str = DEFAULT_CLOUD_POLICY_CONTAINER,
    docker_fallback: bool = DEFAULT_CLOUD_POLICY_DOCKER_FALLBACK,
) -> Dict[str, Any]:
    decide_url = endpoint_url(cloud_policy_url, "/decide")
    payload = cloud_policy_context_payload(state, selection, attacker_execution=attacker_execution)
    payload["metrics"] = {
        "state": state,
    }
    result = post_json_with_container_fallback(
        decide_url,
        payload,
        timeout=timeout,
        docker_container=docker_container,
        docker_fallback=docker_fallback,
        fallback_path="/decide",
    )
    return {**result, "json": parse_response_json(result)}


def ryu_intent_from_cloud_policy(
    decision_result: Dict[str, Any],
    fallback_payload: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    body = decision_result.get("json") or {}
    selected_action = body.get("selected_action") if isinstance(body, dict) else None
    if isinstance(selected_action, dict):
        ryu_intent = selected_action.get("ryu_intent")
        if isinstance(ryu_intent, dict) and ryu_intent.get("action"):
            return dict(ryu_intent)
        if selected_action.get("type"):
            payload = {
                "action": selected_action.get("type"),
                "target": selected_action.get("target", ""),
            }
            payload.update({
                key: value for key, value in selected_action.items()
                if key not in ("type", "reason", "ryu_intent")
            })
            return payload
    return fallback_payload


def execute_attacker(
    selection: Optional[Dict[str, Any]],
    state: Dict[str, Any],
    execute: bool,
    dispatch_url: str,
    cloud_logger_url: str,
    cloud_policy_url: str,
    timeout: float,
) -> Dict[str, Any]:
    plan = build_attacker_execution_plan(selection, state)
    if plan is None:
        return {"status": "no_active_attacker_strategy"}
    if cloud_logger_url:
        plan["bridge_logger_url"] = cloud_logger_url
    if cloud_policy_url:
        plan["bridge_policy_url"] = cloud_policy_url
    if not execute:
        return {"status": "dry_run", "plan": plan}
    if not dispatch_url:
        return {
            "status": "not_dispatched",
            "reason": "set --attacker-dispatch-url to send the Caldera execution plan",
            "plan": plan,
        }
    result = post_json(dispatch_url, plan, timeout=timeout)
    status = "dispatched" if result.get("ok") else "dispatch_failed"
    return {"status": status, "plan": plan, "post_result": result}


def execute_defender(
    selection: Optional[Dict[str, Any]],
    selection_pair: Dict[str, Any],
    state: Dict[str, Any],
    attacker_execution: Optional[Dict[str, Any]],
    execute: bool,
    ryu_action_url: str,
    cloud_policy_url: str,
    timeout: float,
    cloud_policy_docker_container: str,
    cloud_policy_docker_fallback: bool,
) -> Dict[str, Any]:
    payload = action_payload_from_defender(selection)
    if payload is None:
        return {"status": "no_active_defender_strategy"}

    policy_context_result = None
    policy_decision_result = None
    if cloud_policy_url:
        policy_context_result = post_cloud_policy_context(
            cloud_policy_url,
            state,
            selection_pair,
            attacker_execution,
            timeout=timeout,
            docker_container=cloud_policy_docker_container,
            docker_fallback=cloud_policy_docker_fallback,
        )
        policy_decision_result = ask_cloud_policy_decision(
            cloud_policy_url,
            state,
            selection_pair,
            attacker_execution,
            timeout=timeout,
            docker_container=cloud_policy_docker_container,
            docker_fallback=cloud_policy_docker_fallback,
        )
        payload = ryu_intent_from_cloud_policy(policy_decision_result, payload) or payload

    if payload.get("action") == "observe":
        return {
            "status": "observe_only",
            "payload": payload,
            "cloud_policy_context": policy_context_result,
            "cloud_policy_decision": policy_decision_result,
        }
    if not execute:
        return {
            "status": "dry_run",
            "payload": payload,
            "cloud_policy_context": policy_context_result,
            "cloud_policy_decision": policy_decision_result,
        }
    result = post_json(ryu_action_url, payload, timeout=timeout)
    status = "executed" if result.get("ok") else "execution_failed"
    return {
        "status": status,
        "payload": payload,
        "cloud_policy_context": policy_context_result,
        "cloud_policy_decision": policy_decision_result,
        "post_result": result,
    }


def defense_action_confirmed(
    defender_execution: Dict[str, Any],
    next_state: Optional[Dict[str, Any]],
) -> bool:
    if defender_execution.get("status") != "executed":
        return False
    if not (defender_execution.get("post_result") or {}).get("ok"):
        return False

    post_body = response_json(defender_execution.get("post_result"))
    overhead = (next_state or {}).get("overhead") or {}
    active_actions = safe_int(
        post_body.get("active_policy_actions"),
        safe_int(overhead.get("controller_active_actions")),
    )
    flow_rules = safe_int(
        post_body.get("flow_rules_installed"),
        safe_int(overhead.get("flow_rules_installed")),
    )
    meters_added = safe_int(
        post_body.get("meters_added"),
        safe_int(overhead.get("meters_added")),
    )
    installed_status = str(post_body.get("status", "")).lower() in (
        "installed",
        "accepted",
    )
    return active_actions > 0 and (flow_rules > 0 or meters_added > 0 or installed_status)


def defense_result_event_payload(
    state: Dict[str, Any],
    next_state: Optional[Dict[str, Any]],
    selection: Dict[str, Any],
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    defender_execution = execution.get("defender") or {}
    attacker_execution = execution.get("attacker") or {}
    payload = dict(defender_execution.get("payload") or {})
    post_body = response_json(defender_execution.get("post_result"))
    overhead = (next_state or {}).get("overhead") or {}
    action = str(payload.get("action") or post_body.get("action") or "unknown")
    target = str(payload.get("target") or post_body.get("target") or state.get("entry_node") or "unknown")
    active_actions = safe_int(
        post_body.get("active_policy_actions"),
        safe_int(overhead.get("controller_active_actions")),
    )
    flow_rules = safe_int(
        post_body.get("flow_rules_installed"),
        safe_int(overhead.get("flow_rules_installed")),
    )
    meters_added = safe_int(
        post_body.get("meters_added"),
        safe_int(overhead.get("meters_added")),
    )
    apply_ms = safe_float(
        post_body.get("ryu_apply_duration_ms"),
        safe_float(overhead.get("controller_apply_ms")),
    )
    operation_id = (
        response_json((attacker_execution.get("post_result") or {})).get("operation_id")
        or response_json((attacker_execution.get("post_result") or {})).get("id")
        or f"strategy-{state.get('scenario_id', 'unknown')}"
    )
    isolation_actions = {"quarantine_sensor", "isolate_sensor"}
    drop_rules_active = bool((next_state or {}).get("drop_rules_active")) or action in isolation_actions
    counters_stopped = bool((next_state or {}).get("counters_stopped")) or action in isolation_actions

    return {
        "event_type": "defense_result",
        "tool": "ryu_mtd",
        "operation_id": operation_id,
        "scenario_id": state.get("scenario_id"),
        "defense_action": action,
        "target": target,
        "source_strategy_id": ((selection.get("defender") or {}).get("id") or ""),
        "success": True,
        "defense_success": True,
        "signals": {
            "active_policy_actions": active_actions,
            "controller_action_duration_ms": apply_ms,
            "flow_rules_installed": flow_rules,
            "meters_added": meters_added,
            "drop_rules_active": drop_rules_active,
            "counters_stopped": counters_stopped,
            "rate_limit_active": action == "rate_limit" and active_actions > 0,
            "target_ips_count": len(post_body.get("target_ips") or []),
        },
        "timestamp": utc_now_iso(),
    }


def post_defense_result_event(
    cloud_logger_url: str,
    state: Dict[str, Any],
    next_state: Optional[Dict[str, Any]],
    selection: Dict[str, Any],
    execution: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    if not cloud_logger_url:
        return {
            "status": "skipped",
            "reason": "set --cloud-logger-url to auto-post defense_result events",
        }

    defender_execution = execution.get("defender") or {}
    if not defense_action_confirmed(defender_execution, next_state):
        return {
            "status": "skipped",
            "reason": "defender action was not confirmed by Ryu/controller metrics",
        }

    payload = defense_result_event_payload(state, next_state, selection, execution)
    result = post_json(endpoint_url(cloud_logger_url, "/attack/event"), payload, timeout=timeout)
    return {
        "status": "posted" if result.get("ok") else "post_failed",
        "payload": payload,
        "post_result": result,
    }


def run_game_stage(args: argparse.Namespace) -> Dict[str, Any]:
    manager = StrategyManager.from_file(args.strategy_space)
    constraints = {
        "strict_preconditions": args.strict_preconditions,
        "max_attack_cost": args.max_attack_cost,
        "max_defense_cost": args.max_defense_cost,
        "allow_disruptive_defense": not args.no_disruptive_defense,
    }
    state = build_state(
        core_url=None if args.offline else args.core_url,
        mtd_metrics_url=None if args.offline else args.mtd_metrics_url,
        mtd_status_url=None if args.offline else args.mtd_status_url,
        scenario_id=args.scenario_id or None,
        scenario_registry_path=args.scenario_registry,
        mulval_policy_path=args.mulval_policy,
        timeout=args.timeout_seconds,
        constraints=constraints,
        core_docker_fallback=not args.no_core_docker_fallback,
        cloud_metrics_container=args.cloud_metrics_container,
    )
    active = manager.active_lists(state, strict_preconditions=args.strict_preconditions)
    previous_population = load_population(None if args.no_population_load else args.population_file)
    game = evolutionary_step(
        active["attackers"],
        active["defenders"],
        state,
        previous_population=previous_population,
        parameters=manager.parameters,
    )
    selection_mode = args.selection_mode or manager.parameters.get("selection_mode", "dominant")
    selection = select_pair(
        active["attackers"],
        active["defenders"],
        game,
        mode=selection_mode,
        random_seed=args.random_seed,
    )

    attacker_execution = execute_attacker(
        selection.get("attacker"),
        state,
        execute=args.execute_attacker,
        dispatch_url=args.attacker_dispatch_url,
        cloud_logger_url=args.cloud_logger_url,
        cloud_policy_url=args.cloud_policy_url,
        timeout=args.timeout_seconds,
    )
    defender_execution = execute_defender(
        selection.get("defender"),
        selection,
        state,
        attacker_execution=attacker_execution,
        execute=args.execute_defender,
        ryu_action_url=args.ryu_action_url,
        cloud_policy_url=args.cloud_policy_url,
        timeout=args.timeout_seconds,
        cloud_policy_docker_container=args.cloud_policy_docker_container,
        cloud_policy_docker_fallback=not args.no_cloud_policy_docker_fallback,
    )
    execution = {
        "attacker": attacker_execution,
        "defender": defender_execution,
    }

    if not args.offline and not args.no_auto_defense_event:
        execution["defender"]["defense_event"] = post_defense_result_event(
            args.cloud_logger_url,
            state,
            None,
            selection,
            execution,
            args.timeout_seconds,
        )

    next_state = None
    if not args.no_observe_next_state:
        if args.observe_delay_seconds > 0:
            time.sleep(args.observe_delay_seconds)
        next_state = build_state(
            core_url=None if args.offline else args.core_url,
            mtd_metrics_url=None if args.offline else args.mtd_metrics_url,
            mtd_status_url=None if args.offline else args.mtd_status_url,
            scenario_id=args.scenario_id or state.get("scenario_id"),
            scenario_registry_path=args.scenario_registry,
            mulval_policy_path=args.mulval_policy,
            timeout=args.timeout_seconds,
            constraints=constraints,
            core_docker_fallback=not args.no_core_docker_fallback,
            cloud_metrics_container=args.cloud_metrics_container,
        )

    compact_pair = {
        "attacker": compact_selection(selection.get("attacker")),
        "defender": compact_selection(selection.get("defender")),
    }
    transition = build_transition_record(
        state,
        next_state,
        compact_pair,
        execution,
        game,
    )
    decision_trace = build_decision_trace_record(
        state,
        next_state,
        compact_pair,
        execution,
        game,
        transition_id=transition.get("transition_id", ""),
    )

    result = {
        "schema_version": "llm-mtd-strategy-stage-result-v1",
        "state": state,
        "next_state": next_state,
        "active": {
            "attacker_ids": active["attacker_ids"],
            "defender_ids": active["defender_ids"],
            "attackers": active["attackers"],
            "defenders": active["defenders"],
        },
        "game": game,
        "selection": compact_pair,
        "execution": execution,
        "cloud_policy_post_result": execution["defender"].get("cloud_policy_context"),
        "transition": transition,
        "decision_trace": decision_trace,
    }

    skip_persistence = should_skip_persistence_for_state(state)
    result["persistence"] = {
        "skipped": skip_persistence,
        "reason": (
            "source ingestion failed with zero path stage, empty attack/defense "
            "metrics, and all-zero workload"
            if skip_persistence
            else ""
        ),
        "population_saved": False,
        "stage_logged": False,
        "decision_trace_logged": False,
    }

    if not args.no_save_population and not skip_persistence:
        save_population(args.population_file, game, result)
        result["persistence"]["population_saved"] = True
    if not args.no_stage_log and not skip_persistence:
        transition = append_transition(args.stage_log, transition)
        result["transition"] = transition
        result["persistence"]["stage_logged"] = True
    if not args.no_decision_trace_log and not skip_persistence:
        if transition.get("stage_id") and not decision_trace.get("stage_id"):
            decision_trace["stage_id"] = transition["stage_id"]
        decision_trace = append_decision_trace(args.decision_trace_log, decision_trace)
        result["decision_trace"] = decision_trace
        result["persistence"]["decision_trace_logged"] = True

    return result


def run_stage(args: argparse.Namespace) -> Dict[str, Any]:
    return run_game_stage(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one LLM_MTD_emo evolutionary strategy-selection stage."
    )
    parser.add_argument("--strategy-space", type=Path, default=DEFAULT_STRATEGY_SPACE)
    parser.add_argument("--scenario-registry", type=Path, default=DEFAULT_SCENARIO_REGISTRY)
    parser.add_argument("--mulval-policy", type=Path, default=DEFAULT_MULVAL_POLICY)
    parser.add_argument("--scenario-id", default="")
    parser.add_argument("--core-url", default=DEFAULT_CORE_URL)
    parser.add_argument("--mtd-metrics-url", default=DEFAULT_MTD_METRICS_URL)
    parser.add_argument("--mtd-status-url", default=DEFAULT_MTD_STATUS_URL)
    parser.add_argument("--cloud-metrics-container", default=DEFAULT_CLOUD_METRICS_CONTAINER)
    parser.add_argument(
        "--no-core-docker-fallback",
        action="store_true",
        default=not DEFAULT_CORE_DOCKER_FALLBACK,
        help="Disable Docker fallback through mn.cloud_metrics when /core is unreachable from the host.",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument("--strict-preconditions", action="store_true")
    parser.add_argument("--max-attack-cost", type=float, default=1.0)
    parser.add_argument("--max-defense-cost", type=float, default=1.0)
    parser.add_argument("--no-disruptive-defense", action="store_true")
    parser.add_argument("--selection-mode", choices=["dominant", "sample"], default="")
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--population-file", type=Path, default=DEFAULT_POPULATION_STATE)
    parser.add_argument("--no-population-load", action="store_true")
    parser.add_argument("--no-save-population", action="store_true")
    parser.add_argument("--stage-log", type=Path, default=DEFAULT_STAGE_LOG)
    parser.add_argument("--no-stage-log", action="store_true")
    parser.add_argument("--decision-trace-log", type=Path, default=DEFAULT_DECISION_TRACE_LOG)
    parser.add_argument("--no-decision-trace-log", action="store_true")
    parser.add_argument("--observe-delay-seconds", type=float, default=0.0)
    parser.add_argument("--no-observe-next-state", action="store_true")
    parser.add_argument("--execute-attacker", action="store_true")
    parser.add_argument("--attacker-dispatch-url", default=DEFAULT_ATTACKER_DISPATCH_URL)
    parser.add_argument("--execute-defender", action="store_true")
    parser.add_argument("--ryu-action-url", default=DEFAULT_RYU_ACTION_URL)
    parser.add_argument(
        "--cloud-logger-url",
        default=DEFAULT_CLOUD_LOGGER_URL,
        help=(
            "Optional cloud_logger base URL or /attack/event URL. When set, "
            "successful non-observe defender actions are recorded as defense_result events."
        ),
    )
    parser.add_argument(
        "--no-auto-defense-event",
        action="store_true",
        help="Disable automatic defense_result posting after confirmed Ryu defender actions.",
    )
    parser.add_argument(
        "--cloud-policy-url",
        default="",
        help="Optional cloud_policy base URL or /context URL for posting selected strategy context.",
    )
    parser.add_argument("--cloud-policy-docker-container", default=DEFAULT_CLOUD_POLICY_CONTAINER)
    parser.add_argument(
        "--no-cloud-policy-docker-fallback",
        action="store_true",
        default=not DEFAULT_CLOUD_POLICY_DOCKER_FALLBACK,
        help="Disable Docker fallback through mn.cloud_policy when cloud_policy is unreachable from the host.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_stage(args)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
