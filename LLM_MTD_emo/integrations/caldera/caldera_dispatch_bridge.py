#!/usr/bin/env python3
"""Dispatch strategy-layer attacker plans into Caldera operations.

The strategy runtime sends the selected attacker plan here with:

    --execute-attacker
    --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch

The bridge creates a Caldera operation, posts attack_start immediately, then
polls the operation in the background and posts attack_result when Caldera
finishes or times out.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_REGISTRY = REPO_ROOT / "integrations" / "attack_scenarios.json"
DEFAULT_CALDERA_URL = os.environ.get("CALDERA_BASE_URL", "http://127.0.0.1:8888")
DEFAULT_LOGGER_URL = os.environ.get(
    "CALDERA_BRIDGE_LOGGER_URL",
    os.environ.get("CLOUD_LOGGER_URL", ""),
)
DEFAULT_POLICY_URL = os.environ.get(
    "CALDERA_BRIDGE_POLICY_URL",
    os.environ.get("CLOUD_POLICY_URL", ""),
)
DEFAULT_API_KEY = os.environ.get("CALDERA_API_KEY") or os.environ.get("API_TOKEN", "")
DEFAULT_USERNAME = os.environ.get("CALDERA_USERNAME", "")
DEFAULT_PASSWORD = os.environ.get("CALDERA_PASSWORD", "")
DEFAULT_GROUP = os.environ.get("CALDERA_GROUP", "red")
DEFAULT_PORT = int(os.environ.get("CALDERA_DISPATCH_PORT", "9000"))
TERMINAL_STATES = {"finished", "cleanup", "out_of_time"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def endpoint_url(url: str, fallback_path: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.path and parsed.path != "/":
        return url
    return url.rstrip("/") + fallback_path


def configured_callback_url(
    plan: Dict[str, Any],
    plan_key: str,
    default_url: str,
) -> str:
    value = plan.get(plan_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default_url


def unique_nonempty_strings(values: Iterable[Any]) -> List[str]:
    unique: List[str] = []
    seen = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(normalized)
    return unique


def post_json(url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "url": url,
                "body": response.read().decode("utf-8", errors="replace"),
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
    except Exception as error:
        return {"ok": False, "status": 0, "url": url, "body": "", "error": str(error)}


class CalderaSession:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        username: str = "",
        password: str = "",
        timeout: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.username = username
        self.password = password
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self._logged_in = False

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        form: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        self.ensure_auth()
        url = f"{self.base_url}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if self.api_key:
            headers["KEY"] = self.api_key

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed: Any
                try:
                    parsed = json.loads(body or "{}")
                except json.JSONDecodeError:
                    parsed = body
                return {
                    "ok": 200 <= response.status < 300,
                    "status": response.status,
                    "url": url,
                    "json": parsed,
                    "body": body,
                    "error": "",
                }
        except urllib.error.HTTPError as error:
            return {
                "ok": False,
                "status": error.code,
                "url": url,
                "json": {},
                "body": error.read().decode("utf-8", errors="replace"),
                "error": str(error),
            }
        except Exception as error:
            return {
                "ok": False,
                "status": 0,
                "url": url,
                "json": {},
                "body": "",
                "error": str(error),
            }

    def ensure_auth(self) -> None:
        if self.api_key or self._logged_in or not (self.username and self.password):
            return
        result = self.login()
        if result.get("ok") or result.get("status") in (200, 302):
            self._logged_in = True

    def login(self) -> Dict[str, Any]:
        url = f"{self.base_url}/enter"
        data = urllib.parse.urlencode(
            {"username": self.username, "password": self.password}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return {
                    "ok": 200 <= response.status < 400,
                    "status": response.status,
                    "url": url,
                    "body": response.read().decode("utf-8", errors="replace"),
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
        except Exception as error:
            return {"ok": False, "status": 0, "url": url, "body": "", "error": str(error)}


def load_scenario(registry_path: Path, scenario_id: str) -> Dict[str, Any]:
    scenarios = read_json_file(registry_path, default=[])
    if not isinstance(scenarios, list):
        return {}
    for scenario in scenarios:
        if scenario.get("scenario_id") == scenario_id:
            return scenario
    return {}


def as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def normalized_link_status(link: Dict[str, Any]) -> str:
    return str((link or {}).get("status", "")).strip().lower()


def link_is_cleanup(link: Dict[str, Any]) -> bool:
    cleanup = (link or {}).get("cleanup")
    if isinstance(cleanup, bool):
        return cleanup
    if isinstance(cleanup, (int, float)):
        return cleanup != 0
    if isinstance(cleanup, str):
        normalized = cleanup.strip().lower()
        return bool(normalized) and normalized not in ("0", "false", "no")
    return False


def link_is_failed(link: Dict[str, Any]) -> bool:
    return normalized_link_status(link) in ("-3", "failed", "error")


def link_is_completed(link: Dict[str, Any]) -> bool:
    if normalized_link_status(link) in ("0", "1", "success", "completed"):
        return True
    return bool((link or {}).get("finish"))


def link_is_successful_execution(link: Dict[str, Any]) -> bool:
    if link_is_cleanup(link):
        return False
    status = normalized_link_status(link)
    if status in ("1", "success", "completed"):
        return True
    return (
        status == "0"
        and bool((link or {}).get("finish"))
        and bool(str((link or {}).get("command", "")).strip())
    )


def summarize_chain(operation: Dict[str, Any]) -> Dict[str, int]:
    chain = as_list(operation.get("chain"))
    ability_links = [link for link in chain if isinstance(link, dict) and not link_is_cleanup(link)]
    cleanup_links = [link for link in chain if isinstance(link, dict) and link_is_cleanup(link)]
    completed_links = [link for link in ability_links if link_is_completed(link)]
    successful_links = [link for link in ability_links if link_is_successful_execution(link)]
    failed_links = [link for link in ability_links if link_is_failed(link)]
    return {
        "link_count": len(chain),
        "ability_link_count": len(ability_links),
        "cleanup_link_count": len(cleanup_links),
        "completed_link_count": len(completed_links),
        "successful_link_count": len(successful_links),
        "failed_link_count": len(failed_links),
    }


def value_matches(candidate: Any, values: Iterable[str]) -> bool:
    normalized = str(candidate or "").strip().lower()
    return bool(normalized) and normalized in {str(value).strip().lower() for value in values if value}


def first_matching(
    items: Iterable[Dict[str, Any]],
    keys: Iterable[str],
    values: Iterable[str],
) -> Dict[str, Any]:
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            if value_matches(item.get(key), values):
                return item
    return {}


def candidate_target_hosts(plan: Dict[str, Any]) -> List[str]:
    explicit_targets = unique_nonempty_strings(as_list(plan.get("caldera_target_hosts")))
    if explicit_targets:
        return explicit_targets

    executor = plan.get("executor") if isinstance(plan.get("executor"), dict) else {}
    plan_env = plan.get("caldera_env") if isinstance(plan.get("caldera_env"), dict) else {}
    executor_env = executor.get("env") if isinstance(executor.get("env"), dict) else {}

    inferred = unique_nonempty_strings(
        [
            plan_env.get("ENTRY_NODE"),
            plan_env.get("ATTACK_SENSOR_ID"),
            plan_env.get("SENSOR_ID"),
            executor_env.get("ENTRY_NODE"),
            executor_env.get("ATTACK_SENSOR_ID"),
            executor_env.get("SENSOR_ID"),
            plan.get("entry_node"),
        ]
    )
    if inferred:
        return inferred

    path = as_list(plan.get("path"))
    if path:
        return unique_nonempty_strings([path[0]])
    return []


def agent_is_alive(agent: Dict[str, Any]) -> bool:
    return str((agent or {}).get("status", "")).strip().lower() == "alive"


def agent_matches_host(agent: Dict[str, Any], host_names: Iterable[str]) -> bool:
    host = str((agent or {}).get("host", "")).strip()
    display_name = str((agent or {}).get("display_name", "")).strip()
    if value_matches(host, host_names):
        return True
    lowered_display = display_name.lower()
    for candidate in host_names:
        normalized = str(candidate or "").strip().lower()
        if normalized and lowered_display.startswith(f"{normalized}$"):
            return True
    return False


def live_agents_for_hosts(
    session: CalderaSession,
    host_names: Iterable[str],
) -> Dict[str, Any]:
    targets = unique_nonempty_strings(host_names)
    result = session.request("GET", "/api/v2/agents")
    agents = as_list(result.get("json"))
    matched = [
        agent
        for agent in agents
        if isinstance(agent, dict)
        and agent.get("paw")
        and agent_is_alive(agent)
        and agent_matches_host(agent, targets)
    ]
    return {
        "query_result": result,
        "target_hosts": targets,
        "agents": matched,
    }


def patch_agent_group(
    session: CalderaSession,
    paw: str,
    group: str,
) -> Dict[str, Any]:
    return session.request("PATCH", f"/api/v2/agents/{paw}", {"group": group})


def restore_agent_groups(
    session: CalderaSession,
    assignments: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for assignment in assignments:
        paw = str(assignment.get("paw", "")).strip()
        original_group = str(assignment.get("original_group", "")).strip()
        if not paw:
            continue
        result = patch_agent_group(session, paw, original_group)
        results.append(
            {
                "paw": paw,
                "host": assignment.get("host", ""),
                "group": original_group,
                "result": result,
            }
        )
    return results


def stage_targeted_operation_group(
    session: CalderaSession,
    plan: Dict[str, Any],
    fallback_group: str,
) -> Dict[str, Any]:
    target_hosts = candidate_target_hosts(plan)
    if not target_hosts:
        return {
            "mode": "default_group",
            "operation_group": fallback_group,
            "target_hosts": [],
            "selected_agents": [],
            "group_assignments": [],
        }

    agent_selection = live_agents_for_hosts(session, target_hosts)
    selected_agents = agent_selection["agents"]
    if not selected_agents:
        return {
            "mode": "default_group",
            "operation_group": fallback_group,
            "target_hosts": target_hosts,
            "selected_agents": [],
            "group_assignments": [],
            "agent_query": agent_selection["query_result"],
        }

    scoped_group = f"llm-mtd-{uuid.uuid4().hex[:10]}"
    assignments: List[Dict[str, Any]] = []
    patch_results: List[Dict[str, Any]] = []
    for agent in selected_agents:
        paw = str(agent.get("paw", "")).strip()
        if not paw:
            continue
        patch_result = patch_agent_group(session, paw, scoped_group)
        patch_results.append(
            {
                "paw": paw,
                "host": agent.get("host", ""),
                "group": scoped_group,
                "result": patch_result,
            }
        )
        if not patch_result.get("ok"):
            restore_results = restore_agent_groups(session, assignments)
            return {
                "mode": "default_group",
                "operation_group": fallback_group,
                "target_hosts": target_hosts,
                "selected_agents": [
                    {
                        "paw": item.get("paw", ""),
                        "host": item.get("host", ""),
                        "group": item.get("group", ""),
                    }
                    for item in selected_agents
                ],
                "group_assignments": [],
                "patch_results": patch_results,
                "restore_results": restore_results,
                "error": "failed to stage targeted Caldera group",
            }
        assignments.append(
            {
                "paw": paw,
                "host": agent.get("host", ""),
                "original_group": str(agent.get("group", "")).strip(),
            }
        )

    return {
        "mode": "scoped_group",
        "operation_group": scoped_group,
        "target_hosts": target_hosts,
        "selected_agents": [
            {
                "paw": item.get("paw", ""),
                "host": item.get("host", ""),
                "group": item.get("group", ""),
            }
            for item in selected_agents
        ],
        "group_assignments": assignments,
        "patch_results": patch_results,
    }


def select_adversary(session: CalderaSession, plan: Dict[str, Any]) -> Dict[str, Any]:
    result = session.request("GET", "/api/v2/adversaries")
    adversaries = as_list(result.get("json"))
    executor = plan.get("executor") if isinstance(plan.get("executor"), dict) else {}
    candidates = [
        plan.get("caldera_adversary_yaml_id"),
        plan.get("caldera_adversary"),
        executor.get("adversary_yaml_id"),
        executor.get("name"),
    ]
    match = first_matching(adversaries, ("adversary_id", "id", "name"), candidates)
    if match:
        return {"adversary_id": match.get("adversary_id") or match.get("id")}
    if plan.get("caldera_adversary_yaml_id"):
        return {"adversary_id": plan["caldera_adversary_yaml_id"]}
    return {"adversary_id": "ad-hoc"}


def select_named_resource(
    session: CalderaSession,
    path: str,
    name_candidates: Iterable[str],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    result = session.request("GET", path)
    items = as_list(result.get("json"))
    match = first_matching(items, ("name", "id"), name_candidates)
    if match:
        resource_id = match.get("id") or match.get("name")
        return {"id": resource_id} if resource_id else fallback
    if items and isinstance(items[0], dict):
        resource_id = items[0].get("id") or items[0].get("name")
        return {"id": resource_id} if resource_id else fallback
    return fallback


def operation_payload(session: CalderaSession, plan: Dict[str, Any], group: str) -> Dict[str, Any]:
    operation_id = f"llm-mtd-{uuid.uuid4().hex[:12]}"
    return {
        "name": f"{operation_id} {plan.get('strategy_id', 'attack')}",
        "autonomous": int(os.environ.get("CALDERA_AUTONOMOUS", "1")),
        "use_learning_parsers": True,
        "auto_close": bool(os.environ.get("CALDERA_AUTO_CLOSE", "1") != "0"),
        "jitter": os.environ.get("CALDERA_JITTER", "2/8"),
        "state": os.environ.get("CALDERA_OPERATION_STATE", "running"),
        "visibility": int(os.environ.get("CALDERA_VISIBILITY", "51")),
        "obfuscator": select_named_resource(
            session,
            "/api/v2/obfuscators",
            ("plain-text", "none", ""),
            {"id": "plain-text"},
        ).get("id", "plain-text"),
        "source": select_named_resource(
            session,
            "/api/v2/sources",
            ("basic", "default", ""),
            {"id": "basic"},
        ),
        "planner": select_named_resource(
            session,
            "/api/v2/planners",
            ("atomic", "batch", ""),
            {"id": "atomic"},
        ),
        "adversary": select_adversary(session, plan),
        "group": group,
    }


def post_attack_start(
    logger_url: str,
    plan: Dict[str, Any],
    operation_id: str,
    timeout: float,
) -> Dict[str, Any]:
    if not logger_url:
        return {"status": "skipped", "reason": "logger URL not configured"}
    event = {
        "event_type": "attack_start",
        "tool": "caldera",
        "operation_id": operation_id,
        "scenario_id": plan.get("scenario_id"),
        "entry_node": (plan.get("caldera_env") or {}).get("ENTRY_NODE") or "",
        "attempted_path": plan.get("path") or [],
        "live_attack_type": plan.get("live_attack_type", ""),
        "adversary_id": plan.get("caldera_adversary") or plan.get("caldera_adversary_yaml_id") or "",
        "timestamp": utc_now_iso(),
    }
    result = post_json(endpoint_url(logger_url, "/attack/event"), event, timeout)
    return {"status": "posted" if result.get("ok") else "post_failed", "payload": event, "post_result": result}


def operation_success(operation: Dict[str, Any]) -> bool:
    state = str(operation.get("state", "")).lower()
    if state not in ("finished", "cleanup"):
        return False
    summary = summarize_chain(operation)
    if summary["failed_link_count"] > 0:
        return False
    if summary["ability_link_count"] == 0:
        return True
    return (
        summary["completed_link_count"] > 0
        and summary["successful_link_count"] > 0
    )


def build_attack_result(plan: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
    path = as_list(plan.get("path"))
    expected = set(as_list(plan.get("expected_effects")))
    success = operation_success(operation)
    chain_summary = summarize_chain(operation)
    return {
        "event_type": "attack_result",
        "tool": "caldera",
        "operation_id": operation.get("id") or operation.get("name", ""),
        "scenario_id": plan.get("scenario_id"),
        "entry_node": (plan.get("caldera_env") or {}).get("ENTRY_NODE") or (path[0] if path else ""),
        "attempted_path": path,
        "target_asset": path[-1] if path else "",
        "live_attack_type": plan.get("live_attack_type", ""),
        "adversary_id": plan.get("caldera_adversary") or plan.get("caldera_adversary_yaml_id") or "",
        "success": success,
        "gateway_seen": success and ("gateway_seen" in expected or len(path) > 1),
        "worker_seen": success and ("worker_seen" in expected or len(path) > 2),
        "cloud_seen": success and ("cloud_seen" in expected or len(path) > 3),
        "attack_effect_success": success,
        "signals": {
            "operation_state": operation.get("state", ""),
            **chain_summary,
        },
        "timestamp": utc_now_iso(),
    }


def post_attack_result(
    logger_url: str,
    policy_url: str,
    plan: Dict[str, Any],
    operation: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    result_payload = build_attack_result(plan, operation)
    posts = {}
    if logger_url:
        posts["logger"] = post_json(
            endpoint_url(logger_url, "/attack/event"),
            result_payload,
            timeout,
        )
    if policy_url:
        posts["policy"] = post_json(
            endpoint_url(policy_url, "/context"),
            {"caldera_result": result_payload, "observe_only": True},
            timeout,
        )
    return {"payload": result_payload, "posts": posts}


def poll_operation(
    session: CalderaSession,
    operation_id: str,
    plan: Dict[str, Any],
    logger_url: str,
    policy_url: str,
    group_assignments: List[Dict[str, Any]],
    interval_seconds: float,
    max_seconds: float,
    timeout: float,
) -> None:
    deadline = time.monotonic() + max_seconds
    last_operation: Dict[str, Any] = {"id": operation_id, "state": "unknown", "chain": []}
    try:
        while time.monotonic() < deadline:
            time.sleep(interval_seconds)
            result = session.request("GET", f"/api/v2/operations/{operation_id}")
            if isinstance(result.get("json"), dict):
                last_operation = result["json"]
            state = str(last_operation.get("state", "")).lower()
            if state in TERMINAL_STATES:
                post_attack_result(logger_url, policy_url, plan, last_operation, timeout)
                return

        last_operation["state"] = last_operation.get("state") or "poll_timeout"
        post_attack_result(logger_url, policy_url, plan, last_operation, timeout)
    finally:
        if group_assignments:
            restore_agent_groups(session, group_assignments)


class DispatchHandler(BaseHTTPRequestHandler):
    config: argparse.Namespace

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path not in ("/health", "/caldera/health"):
            self.send_json(404, {"error": "not found"})
            return
        self.send_json(
            200,
            {
                "service": "caldera-dispatch-bridge",
                "caldera_url": self.config.caldera_url,
                "logger_url": self.config.logger_url,
                "policy_url": self.config.policy_url,
                "api_key_configured": bool(self.config.api_key),
                "username_configured": bool(self.config.username),
            },
        )

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/caldera/dispatch":
            self.send_json(404, {"error": "not found"})
            return

        plan = self.read_json()
        if not plan.get("strategy_id"):
            self.send_json(400, {"error": "missing selected attacker plan"})
            return

        session = CalderaSession(
            self.config.caldera_url,
            api_key=self.config.api_key,
            username=self.config.username,
            password=self.config.password,
            timeout=self.config.timeout_seconds,
        )
        logger_url = configured_callback_url(
            plan,
            "bridge_logger_url",
            self.config.logger_url,
        )
        policy_url = configured_callback_url(
            plan,
            "bridge_policy_url",
            self.config.policy_url,
        )
        targeting = stage_targeted_operation_group(session, plan, self.config.group)
        payload = operation_payload(session, plan, targeting["operation_group"])
        launch = session.request("POST", "/api/v2/operations", payload)
        if not launch.get("ok"):
            restore_results = restore_agent_groups(
                session,
                targeting.get("group_assignments", []),
            )
            self.send_json(
                502,
                {
                    "status": "dispatch_failed",
                    "targeting": targeting,
                    "restore_results": restore_results,
                    "operation_payload": payload,
                    "launch_result": launch,
                },
            )
            return

        operation = launch.get("json") if isinstance(launch.get("json"), dict) else {}
        operation_id = operation.get("id") or operation.get("name") or payload["name"]
        attack_start = post_attack_start(
            logger_url,
            plan,
            operation_id,
            self.config.timeout_seconds,
        )
        thread = threading.Thread(
            target=poll_operation,
            args=(
                session,
                operation_id,
                plan,
                logger_url,
                policy_url,
                targeting.get("group_assignments", []),
                self.config.poll_interval_seconds,
                self.config.poll_timeout_seconds,
                self.config.timeout_seconds,
            ),
            daemon=True,
        )
        thread.start()
        self.send_json(
            202,
            {
                "status": "dispatched",
                "operation_id": operation_id,
                "targeting": targeting,
                "operation_payload": payload,
                "launch_result": launch,
                "attack_start": attack_start,
                "polling": "started",
            },
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge strategy attacker plans to Caldera.")
    parser.add_argument("--host", default=os.environ.get("CALDERA_DISPATCH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--caldera-url", default=DEFAULT_CALDERA_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--logger-url", default=DEFAULT_LOGGER_URL)
    parser.add_argument("--policy-url", default=DEFAULT_POLICY_URL)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--poll-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--scenario-registry", type=Path, default=DEFAULT_SCENARIO_REGISTRY)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    DispatchHandler.config = args
    server = ThreadingHTTPServer((args.host, args.port), DispatchHandler)
    print(
        f"Caldera dispatch bridge: http://{args.host}:{args.port}/caldera/dispatch",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
