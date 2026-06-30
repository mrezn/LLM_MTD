"""
environment/attacker_env_controller.py

Attacker-side environment controller for LLM_MTD_modular.

Responsibilities:
  1. Provide the attacker engine with an observable snapshot of the
     environment: reachable nodes, open services, topology, current
     defense posture (partial — attacker is partially blind).
  2. Bridge between the game layer's selected attacker execution plan
     and the Caldera dispatch bridge (POST to /caldera/dispatch).
  3. Monitor environment metrics to detect attack progress signals
     (gateway_received_delta, worker_request_delta, etc.).
  4. Feed attack observations back to the game layer as attack_result
     events.

Interfaces:
  - get_observable_state(timeout) -> dict   Attacker's partial env view
  - dispatch_attack(plan: dict) -> dict     Send plan to Caldera bridge
  - poll_attack_progress(operation_id, timeout) -> dict
  - record_attack_result(result: dict)      Post to cloud_logger

Run standalone:
  python -m environment.attacker_env_controller --status
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Default URLs (overridable via environment variables)
# ---------------------------------------------------------------------------

CALDERA_DISPATCH_URL = os.environ.get(
    "ATTACKER_CALDERA_DISPATCH_URL",
    "http://127.0.0.1:9000",
)
CLOUD_METRICS_URL = os.environ.get(
    "ATTACKER_CLOUD_METRICS_URL",
    "http://127.0.0.1:8088/core",
)
CLOUD_LOGGER_URL = os.environ.get(
    "ATTACKER_CLOUD_LOGGER_URL",
    "",
)
RYU_STATUS_URL = os.environ.get(
    "ATTACKER_RYU_STATUS_URL",
    "http://127.0.0.1:8080/mtd/status",
)
DEFAULT_SCENARIO_REGISTRY = (
    Path(__file__).resolve().parents[1]
    / "attacker"
    / "scenarios"
    / "attack_scenarios.json"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body or "{}")
    except Exception:
        return {}


def _post_json(url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
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
    except Exception as error:
        return {"ok": False, "status": 0, "url": url, "body": "", "error": str(error)}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(_safe_float(value, float(default))))
    except (TypeError, ValueError):
        return default


def _endpoint_url(url: str, fallback_path: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url
    return url.rstrip("/") + fallback_path


def _read_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class AttackerEnvController:
    """Attacker-side interface to the live environment.

    This controller gives the attacker engine a limited, partially-blind
    view of the environment — it can infer some defense is present from
    Ryu active-action counts, but cannot read the full defender state.

    Parameters
    ----------
    caldera_dispatch_url:
        Base URL of the Caldera dispatch bridge HTTP server.
    cloud_metrics_url:
        URL of the cloud_metrics ``/core`` endpoint.
    cloud_logger_url:
        URL of the cloud_logger service for recording results (optional).
    ryu_status_url:
        URL of the Ryu ``/mtd/status`` endpoint for partial defense inference.
    scenario_registry_path:
        Path to ``attack_scenarios.json``.  Defaults to the bundled file
        inside ``attacker/scenarios/``.
    timeout:
        Default HTTP timeout in seconds.
    """

    def __init__(
        self,
        caldera_dispatch_url: str = CALDERA_DISPATCH_URL,
        cloud_metrics_url: str = CLOUD_METRICS_URL,
        cloud_logger_url: str = CLOUD_LOGGER_URL,
        ryu_status_url: str = RYU_STATUS_URL,
        scenario_registry_path: Optional[Path] = None,
        timeout: float = 5.0,
    ) -> None:
        self.caldera_dispatch_url = caldera_dispatch_url
        self.cloud_metrics_url = cloud_metrics_url
        self.cloud_logger_url = cloud_logger_url
        self.ryu_status_url = ryu_status_url
        self.scenario_registry_path = scenario_registry_path or DEFAULT_SCENARIO_REGISTRY
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Attacker's observable environment state (partial view)
    # ------------------------------------------------------------------

    def get_observable_state(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Return the attacker's partial view of the environment.

        The attacker can observe:
        - cloud_metrics /core: attack_events (their own past results),
          message_loss_counters (traffic signals), service health.
        - Ryu /mtd/status: number of active defense actions (partial —
          attacker knows *some* defense exists, not what exactly).
        - Topology summary derived from cloud_metrics data.

        The attacker does NOT have access to:
        - Full defender strategy selections
        - Ryu OpenFlow rule details
        - Defense metrics from the defender's perspective

        Returns
        -------
        dict
            Partial environment observation for the attacker engine.
        """
        t = timeout if timeout is not None else self.timeout
        errors: List[str] = []

        # Fetch cloud_metrics core data
        core_data: Dict[str, Any] = {}
        try:
            core_data = _get_json(self.cloud_metrics_url, t)
        except Exception as error:
            errors.append(f"cloud_metrics fetch failed: {error}")

        # Fetch Ryu status (attacker gets only the count of active defenses,
        # not the actual action details)
        ryu_data: Dict[str, Any] = {}
        active_defense_action_count = 0
        try:
            ryu_data = _get_json(self.ryu_status_url, t)
            active_actions = ryu_data.get("active_actions") if isinstance(ryu_data, dict) else {}
            if isinstance(active_actions, dict):
                active_defense_action_count = len(active_actions)
            elif isinstance(active_actions, list):
                active_defense_action_count = len(active_actions)
        except Exception as error:
            errors.append(f"Ryu status fetch failed: {error}")

        def _rows(key: str) -> List[Dict[str, Any]]:
            value = core_data.get(key, [])
            return value if isinstance(value, list) else []

        # Message loss counters — useful for detecting traffic blocking
        msg_rows = _rows("message_loss_counters")
        attack_event_rows = _rows("attack_events")

        # Build topology summary from known IPs in Ryu known_hosts
        known_hosts: Dict[str, Any] = {}
        if isinstance(ryu_data.get("known_hosts"), dict):
            known_hosts = ryu_data["known_hosts"]

        reachable_node_ips = list(known_hosts.keys())

        # Infer service ports from architecture constants (always 8000)
        service_ports: Dict[str, int] = {
            node_ip: 8000 for node_ip in reachable_node_ips
        }

        # MTD posture summary — attacker knows *something* is happening
        mtd_posture_summary = {
            "defense_actions_active": active_defense_action_count > 0,
            "active_action_count_inferred": active_defense_action_count,
            "note": (
                "Attacker cannot determine which actions are active; "
                "only the count is observable from network-level telemetry."
            ),
        }

        # Traffic signals: derive gateway_received, worker_request, cloud deltas
        gateway_received_total = sum(
            _safe_float(r.get("value"))
            for r in msg_rows
            if "received" in str(r.get("metric", ""))
            and "sensors." in str(r.get("metric", ""))
        )
        worker_requests_total = sum(
            _safe_float(r.get("value"))
            for r in msg_rows
            if "requests_total" in str(r.get("metric", ""))
            and r.get("role") == "edge_worker"
        )
        cloud_storage_total = sum(
            _safe_float(r.get("value"))
            for r in msg_rows
            if "storage_confirmations_total" in str(r.get("metric", ""))
        )
        gateway_dropped_total = sum(
            _safe_float(r.get("value"))
            for r in msg_rows
            if "dropped" in str(r.get("metric", ""))
        )

        # Own attack result signals from latest attack_events
        own_attack_results = [
            row for row in attack_event_rows
            if row.get("metric") in (
                "gateway_seen",
                "worker_seen",
                "cloud_seen",
                "success",
                "attack_effect_success",
            )
        ]

        return {
            "schema_version": "llm-mtd-attacker-observation-v1",
            "observed_at": _utc_now_iso(),
            "reachable_node_ips": reachable_node_ips,
            "service_ports": service_ports,
            "mtd_posture_summary": mtd_posture_summary,
            "traffic_signals": {
                "gateway_received_total": gateway_received_total,
                "worker_requests_total": worker_requests_total,
                "cloud_storage_total": cloud_storage_total,
                "gateway_dropped_total": gateway_dropped_total,
                "blocking_inferred": gateway_dropped_total > 0 or active_defense_action_count > 0,
            },
            "own_attack_signals": own_attack_results,
            "source_errors": errors,
        }

    # ------------------------------------------------------------------
    # Attack dispatch
    # ------------------------------------------------------------------

    def dispatch_attack(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """POST an attacker execution plan to the Caldera dispatch bridge.

        Parameters
        ----------
        plan:
            Attack plan dict as produced by
            ``strategy_runtime.build_attacker_execution_plan()``.

        Returns
        -------
        dict
            Dispatch result with keys: ``ok``, ``status``, ``body``,
            ``url``, ``error``.  On success, ``body`` contains the Caldera
            operation info (``operation_id`` etc.).
        """
        dispatch_endpoint = _endpoint_url(self.caldera_dispatch_url, "/caldera/dispatch")
        result = _post_json(dispatch_endpoint, plan, self.timeout)
        try:
            result["body"] = json.loads(result.get("body") or "{}")
        except json.JSONDecodeError:
            pass
        return result

    # ------------------------------------------------------------------
    # Attack progress polling
    # ------------------------------------------------------------------

    def get_attack_progress(
        self,
        operation_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Check if a Caldera operation is still running.

        Polls the Caldera dispatch bridge ``/caldera/health`` endpoint.
        If the bridge is not available or returns an error, checks
        cloud_logger for a matching result record.

        Parameters
        ----------
        operation_id:
            Caldera operation ID returned by :meth:`dispatch_attack`.

        Returns
        -------
        dict
            Status information with keys: ``operation_id``, ``status``,
            ``source``.
        """
        t = timeout if timeout is not None else self.timeout
        health_url = _endpoint_url(self.caldera_dispatch_url, "/caldera/health")
        try:
            health_data = _get_json(health_url, t)
            if health_data:
                return {
                    "operation_id": operation_id,
                    "status": "bridge_alive",
                    "source": "caldera_bridge_health",
                    "bridge_info": health_data,
                }
        except Exception:
            pass

        # Fallback: check cloud_logger for a matching attack_result event
        if self.cloud_logger_url:
            events_url = _endpoint_url(self.cloud_logger_url, "/events") + "?limit=100"
            try:
                events_data = _get_json(events_url, t)
                events = events_data.get("events", [])
                for event in reversed(events):
                    if event.get("operation_id") == operation_id:
                        state = event.get("event_type", "")
                        return {
                            "operation_id": operation_id,
                            "status": "result_found" if "result" in state else state,
                            "source": "cloud_logger",
                            "event": event,
                        }
            except Exception:
                pass

        return {
            "operation_id": operation_id,
            "status": "unknown",
            "source": "no_source_available",
        }

    # ------------------------------------------------------------------
    # Recording attack results
    # ------------------------------------------------------------------

    def record_manual_attack_result(
        self,
        operation_id: str,
        scenario_id: str,
        entry_node: str,
        attempted_path: List[str],
        target_asset: str,
        live_attack_type: str,
        adversary_id: str,
        success: bool,
        gateway_received_delta: int = 0,
        worker_request_delta: int = 0,
        cloud_summary_delta: int = 0,
        gateway_queue_spike: bool = False,
        attack_effect_success: bool = False,
    ) -> Dict[str, Any]:
        """Build and post a manual attack result record to cloud_logger.

        This mirrors ``caldera_client.build_result_record`` but works
        without CLI flags. Useful when tests are run manually and results
        need to be injected into cloud_metrics.

        Returns
        -------
        dict
            The result record that was posted, plus the post result.
        """
        record = {
            "event_type": "attack_result",
            "tool": "caldera",
            "operation_id": operation_id,
            "scenario_id": scenario_id,
            "entry_node": entry_node,
            "attempted_path": attempted_path,
            "target_asset": target_asset,
            "live_attack_type": live_attack_type,
            "adversary_id": adversary_id,
            "success": success,
            "gateway_seen": gateway_received_delta > 0,
            "worker_seen": worker_request_delta > 0,
            "worker_requests_increase": worker_request_delta > 0,
            "cloud_seen": cloud_summary_delta > 0,
            "cloud_summary_rate_changes": cloud_summary_delta > 0,
            "gateway_queue_spike": gateway_queue_spike,
            "attack_effect_success": attack_effect_success,
            "signals": {
                "gateway_received_delta": gateway_received_delta,
                "worker_request_delta": worker_request_delta,
                "cloud_summary_delta": cloud_summary_delta,
                "gateway_queue_spike": gateway_queue_spike,
            },
            "timestamp": _utc_now_iso(),
        }

        post_result: Dict[str, Any] = {"status": "skipped", "reason": "cloud_logger_url not configured"}
        if self.cloud_logger_url:
            logger_endpoint = _endpoint_url(self.cloud_logger_url, "/attack/event")
            post_result = _post_json(logger_endpoint, record, self.timeout)

        return {"record": record, "post_result": post_result}

    # ------------------------------------------------------------------
    # Scenario loading
    # ------------------------------------------------------------------

    def load_scenario(self, scenario_id: str) -> Dict[str, Any]:
        """Load a scenario definition from the attack_scenarios.json registry.

        Parameters
        ----------
        scenario_id:
            The ``scenario_id`` field to look up.

        Returns
        -------
        dict
            The matching scenario dict, or ``{}`` if not found.
        """
        scenarios = _read_json_file(self.scenario_registry_path, default=[])
        if not isinstance(scenarios, list):
            return {}
        for scenario in scenarios:
            if isinstance(scenario, dict) and scenario.get("scenario_id") == scenario_id:
                return scenario
        return {}

    # ------------------------------------------------------------------
    # Attack observation: compute metric deltas
    # ------------------------------------------------------------------

    def build_attack_observation(
        self,
        before_snapshot: Dict[str, Any],
        after_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute metric deltas between two observable state snapshots.

        Used to determine whether an attack made progress by comparing
        traffic signals before and after an attack step.

        Parameters
        ----------
        before_snapshot:
            Snapshot returned by :meth:`get_observable_state` before the attack.
        after_snapshot:
            Snapshot returned by :meth:`get_observable_state` after the attack.

        Returns
        -------
        dict
            Delta signals including ``gateway_received_delta``,
            ``worker_request_delta``, ``cloud_storage_delta``, and
            derived boolean indicators.
        """
        before_signals = before_snapshot.get("traffic_signals") or {}
        after_signals = after_snapshot.get("traffic_signals") or {}

        gateway_received_delta = max(
            0,
            _safe_int(after_signals.get("gateway_received_total"))
            - _safe_int(before_signals.get("gateway_received_total")),
        )
        worker_request_delta = max(
            0,
            _safe_int(after_signals.get("worker_requests_total"))
            - _safe_int(before_signals.get("worker_requests_total")),
        )
        cloud_storage_delta = max(
            0,
            _safe_int(after_signals.get("cloud_storage_total"))
            - _safe_int(before_signals.get("cloud_storage_total")),
        )
        gateway_dropped_delta = max(
            0,
            _safe_int(after_signals.get("gateway_dropped_total"))
            - _safe_int(before_signals.get("gateway_dropped_total")),
        )

        before_defense = (before_snapshot.get("mtd_posture_summary") or {}).get(
            "active_action_count_inferred", 0
        )
        after_defense = (after_snapshot.get("mtd_posture_summary") or {}).get(
            "active_action_count_inferred", 0
        )
        new_defense_actions = max(0, _safe_int(after_defense) - _safe_int(before_defense))

        gateway_seen = gateway_received_delta > 0
        worker_seen = worker_request_delta > 0
        cloud_seen = cloud_storage_delta > 0
        blocking_increased = gateway_dropped_delta > 0 or new_defense_actions > 0

        path_stage = 3 if cloud_seen else 2 if worker_seen else 1 if gateway_seen else 0

        return {
            "observed_at": _utc_now_iso(),
            "gateway_received_delta": gateway_received_delta,
            "worker_request_delta": worker_request_delta,
            "cloud_storage_delta": cloud_storage_delta,
            "gateway_dropped_delta": gateway_dropped_delta,
            "new_defense_actions_inferred": new_defense_actions,
            "gateway_seen": gateway_seen,
            "worker_seen": worker_seen,
            "cloud_seen": cloud_seen,
            "blocking_increased": blocking_increased,
            "inferred_path_stage": path_stage,
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LLM_MTD_modular attacker environment controller."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print attacker observable state and exit.",
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Query and print Caldera agent status without launching attack.",
    )
    parser.add_argument(
        "--caldera-dispatch-url",
        default=CALDERA_DISPATCH_URL,
    )
    parser.add_argument("--cloud-metrics-url", default=CLOUD_METRICS_URL)
    parser.add_argument("--cloud-logger-url", default=CLOUD_LOGGER_URL)
    parser.add_argument("--ryu-status-url", default=RYU_STATUS_URL)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    import sys

    args = _build_arg_parser().parse_args(argv)
    controller = AttackerEnvController(
        caldera_dispatch_url=args.caldera_dispatch_url,
        cloud_metrics_url=args.cloud_metrics_url,
        cloud_logger_url=args.cloud_logger_url,
        ryu_status_url=args.ryu_status_url,
        timeout=args.timeout,
    )

    if args.observe:
        caldera_agents_url = _endpoint_url(args.caldera_dispatch_url, "/caldera/health")
        agent_data = _get_json(caldera_agents_url, args.timeout)
        json.dump(agent_data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    state = controller.get_observable_state()
    json.dump(state, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
