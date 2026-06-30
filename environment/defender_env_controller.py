"""
environment/defender_env_controller.py

Defender-side environment controller for LLM_MTD_modular.

Responsibilities:
  1. Receive MTD action orders from the defender decision engine and apply
     them to the Ryu SDN controller via REST (/mtd/action).
  2. Poll cloud_metrics /core and Ryu /mtd/metrics to build a live
     environment snapshot and stream it to the defender decision engine.
  3. Translate raw environment state into the format expected by the game
     layer (state_builder) and defender decision layer.

Interfaces:
  - apply_action(payload: dict) -> dict   Push an MTD action to Ryu
  - get_env_state(timeout: float) -> dict  Fetch live state for the defender
  - get_ryu_status(timeout: float) -> dict Fetch Ryu topology / active actions
  - stream_state(interval: float, callback)  Run polling loop in background

Run standalone for health-check:
  python -m environment.defender_env_controller --status
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Default URLs (overridable via environment variables)
# ---------------------------------------------------------------------------

RYU_ACTION_URL = os.environ.get(
    "DEFENDER_RYU_ACTION_URL",
    "http://127.0.0.1:8080/mtd/action",
)
RYU_STATUS_URL = os.environ.get(
    "DEFENDER_RYU_STATUS_URL",
    "http://127.0.0.1:8080/mtd/status",
)
RYU_METRICS_URL = os.environ.get(
    "DEFENDER_RYU_METRICS_URL",
    "http://127.0.0.1:8080/mtd/metrics",
)
CORE_URL = os.environ.get(
    "DEFENDER_CORE_URL",
    "http://127.0.0.1:8088/core",
)
DEFAULT_CLOUD_METRICS_CONTAINER = os.environ.get(
    "DEFENDER_CLOUD_METRICS_CONTAINER",
    "mn.cloud_metrics",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _get_text(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _get_json(url: str, timeout: float) -> Dict[str, Any]:
    text = _get_text(url, timeout)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _fetch_json_from_container(
    container_name: str,
    path: str,
    timeout: float,
    errors: List[str],
) -> Dict[str, Any]:
    """Fetch JSON from a service inside a Docker container via docker exec."""
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

    attempt_errors: List[str] = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(timeout + 4.0, 6.0),
            )
        except Exception as error:
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


def _bool_metric(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ok", "success")
    return False


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class DefenderEnvController:
    """Defender-side interface to the live Ryu SDN + cloud_metrics environment.

    Parameters
    ----------
    ryu_action_url:
        REST endpoint for posting MTD actions (``/mtd/action``).
    ryu_status_url:
        REST endpoint for Ryu topology status (``/mtd/status``).
    ryu_metrics_url:
        REST endpoint for Ryu Prometheus metrics (``/mtd/metrics``).
    core_url:
        REST endpoint for cloud_metrics core state (``/core``).
    docker_fallback:
        When ``True``, if a direct HTTP fetch to cloud_metrics fails,
        automatically retry using ``docker exec`` on *cloud_metrics_container*.
    cloud_metrics_container:
        Docker container name for the fallback exec path.
    timeout:
        Default HTTP request timeout in seconds.
    """

    def __init__(
        self,
        ryu_action_url: str = RYU_ACTION_URL,
        ryu_status_url: str = RYU_STATUS_URL,
        ryu_metrics_url: str = RYU_METRICS_URL,
        core_url: str = CORE_URL,
        docker_fallback: bool = True,
        cloud_metrics_container: str = DEFAULT_CLOUD_METRICS_CONTAINER,
        timeout: float = 3.0,
    ) -> None:
        self.ryu_action_url = ryu_action_url
        self.ryu_status_url = ryu_status_url
        self.ryu_metrics_url = ryu_metrics_url
        self.core_url = core_url
        self.docker_fallback = docker_fallback
        self.cloud_metrics_container = cloud_metrics_container
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Defender → Ryu: action dispatch
    # ------------------------------------------------------------------

    def apply_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST an MTD action to the Ryu controller.

        Parameters
        ----------
        payload:
            Action payload dict, e.g.
            ``{"action": "quarantine_sensor", "target": "sen4"}``.

        Returns
        -------
        dict
            Result dict with keys: ``ok`` (bool), ``status`` (HTTP code),
            ``body`` (str or parsed dict), ``url``, ``error``.
        """
        result = _post_json(self.ryu_action_url, payload, self.timeout)
        try:
            result["body"] = json.loads(result["body"] or "{}")
        except json.JSONDecodeError:
            pass
        return result

    def clear_action(self, target: str) -> Dict[str, Any]:
        """Clear all policy rules for *target* from the Ryu controller.

        Sends a ``clear_target_policy`` action to Ryu, which removes all
        OpenFlow rules associated with *target*.

        Parameters
        ----------
        target:
            Node name (e.g. ``"sen4"``), IP address, or Mininet host name
            whose policy rules should be removed.

        Returns
        -------
        dict
            Same structure as :meth:`apply_action`.
        """
        payload = {
            "action": "clear_target_policy",
            "target": target,
            "source": "defender_env_controller",
        }
        return self.apply_action(payload)

    # ------------------------------------------------------------------
    # Environment observation: cloud_metrics /core
    # ------------------------------------------------------------------

    def get_env_state(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Fetch the live environment snapshot from cloud_metrics /core.

        Falls back to ``docker exec`` if the direct HTTP request fails and
        *docker_fallback* is enabled.

        Returns
        -------
        dict
            Raw cloud_metrics /core response, or ``{}`` on failure.
            Includes a ``_source_errors`` key listing any fetch errors.
        """
        t = timeout if timeout is not None else self.timeout
        errors: List[str] = []

        # Try direct HTTP first
        try:
            data = _get_json(self.core_url, t)
            if data:
                data["_source_errors"] = errors
                data["_fetched_at"] = _utc_now_iso()
                return data
            errors.append(f"empty response from {self.core_url}")
        except Exception as error:
            errors.append(f"direct /core fetch failed: {error}")

        # Docker fallback
        if self.docker_fallback and self.cloud_metrics_container:
            fallback_data = _fetch_json_from_container(
                self.cloud_metrics_container,
                "/core",
                t,
                errors,
            )
            if fallback_data:
                fallback_data["_source_errors"] = errors
                fallback_data["_fetched_at"] = _utc_now_iso()
                fallback_data["_docker_fallback_used"] = True
                return fallback_data

        return {"_source_errors": errors, "_fetched_at": _utc_now_iso()}

    # ------------------------------------------------------------------
    # Ryu controller state
    # ------------------------------------------------------------------

    def get_ryu_status(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Fetch the Ryu controller topology and active actions status.

        Returns
        -------
        dict
            Parsed JSON from ``/mtd/status``, or ``{}`` on failure.
        """
        t = timeout if timeout is not None else self.timeout
        try:
            return _get_json(self.ryu_status_url, t)
        except Exception:
            return {}

    def get_ryu_metrics_text(self, timeout: Optional[float] = None) -> str:
        """Fetch the Ryu Prometheus metrics as raw text.

        Returns
        -------
        str
            Raw Prometheus exposition text from ``/mtd/metrics``,
            or empty string on failure.
        """
        t = timeout if timeout is not None else self.timeout
        return _get_text(self.ryu_metrics_url, t)

    def get_active_actions(self, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """Fetch the list of currently active MTD policy actions from Ryu.

        Returns
        -------
        list
            List of active action dicts from Ryu ``/mtd/actions``,
            or empty list on failure.
        """
        t = timeout if timeout is not None else self.timeout
        ryu_actions_url = self.ryu_status_url.replace("/mtd/status", "/mtd/actions")
        try:
            data = _get_json(ryu_actions_url, t)
        except Exception:
            return []
        active = data.get("active_actions")
        if isinstance(active, dict):
            return list(active.values())
        if isinstance(active, list):
            return active
        return []

    # ------------------------------------------------------------------
    # Unified defender state snapshot
    # ------------------------------------------------------------------

    def build_defender_state_snapshot(self) -> Dict[str, Any]:
        """Build a unified state snapshot for the defender decision engine.

        Combines:
        - cloud_metrics ``/core`` (QoS, workload, attack/defense events)
        - Ryu ``/mtd/status`` (topology, active actions, controller metrics)

        The returned dict has keys compatible with the game layer
        ``state_builder`` format so that it can feed directly into the
        strategy engine.

        Returns
        -------
        dict
            Unified state snapshot with keys:
            ``qos``, ``overhead``, ``controller``, ``attack_events``,
            ``defense_active``, ``source_errors``.
        """
        source_errors: List[str] = []
        fetched_at = _utc_now_iso()

        # Fetch cloud_metrics /core
        core_data = self.get_env_state()
        source_errors.extend(core_data.pop("_source_errors", []))

        # Fetch Ryu status
        ryu_status = self.get_ryu_status()

        # Fetch Ryu metrics text
        ryu_metrics_text = self.get_ryu_metrics_text()

        # --- Parse Ryu topology summary ---
        switches = ryu_status.get("switches") if isinstance(ryu_status, dict) else []
        active_actions_dict = ryu_status.get("active_actions") if isinstance(ryu_status, dict) else {}
        active_actions_list: List[Dict[str, Any]] = []
        if isinstance(active_actions_dict, dict):
            active_actions_list = list(active_actions_dict.values())
        elif isinstance(active_actions_dict, list):
            active_actions_list = active_actions_dict

        controller_metrics = ryu_status.get("controller_metrics") if isinstance(ryu_status, dict) else {}
        if not isinstance(controller_metrics, dict):
            controller_metrics = {}

        active_action_count = len(active_actions_list)
        controller_apply_ms = _safe_float(controller_metrics.get("last_action_duration_ms"))
        flow_rules_installed = _safe_int(controller_metrics.get("flow_rules_installed_total"))
        flow_delete_commands = _safe_int(controller_metrics.get("flow_delete_commands_total"))
        meters_added = _safe_int(controller_metrics.get("meters_added_total"))
        controller_reachable = bool(ryu_metrics_text or ryu_status)

        # --- Parse cloud_metrics core data ---
        def _rows(key: str) -> List[Dict[str, Any]]:
            value = core_data.get(key, [])
            return value if isinstance(value, list) else []

        def _safe_val(rows_list: List[Dict[str, Any]], metric_token: str) -> float:
            for row in rows_list:
                if metric_token in str(row.get("metric", "")):
                    return _safe_float(row.get("value"))
            return 0.0

        sensor_edge_rows = _rows("sensor_to_edge_latency_ms")
        edge_cloud_rows = _rows("edge_to_cloud_latency_ms")
        attack_event_rows = _rows("attack_events")
        defense_event_rows = _rows("defense_events")

        sensor_to_edge_latency_ms = (
            sum(_safe_float(r.get("value")) for r in sensor_edge_rows) / max(len(sensor_edge_rows), 1)
            if sensor_edge_rows else 0.0
        )
        edge_to_cloud_latency_ms = (
            sum(_safe_float(r.get("value")) for r in edge_cloud_rows) / max(len(edge_cloud_rows), 1)
            if edge_cloud_rows else 0.0
        )

        # Detect attack signals
        attack_active = any(
            _bool_metric(row.get("value")) and row.get("metric") in ("attack_active",)
            for row in attack_event_rows
        )
        attack_events_count = len(attack_event_rows)

        # Detect defense signals
        defense_success = any(
            _bool_metric(row.get("value")) and "defense_success" in str(row.get("metric", ""))
            for row in defense_event_rows
        )
        drop_rules_active = any(
            _bool_metric(row.get("value")) and "drop_rules_active" in str(row.get("metric", ""))
            for row in defense_event_rows
        )
        counters_stopped = any(
            _bool_metric(row.get("value")) and "counters_stopped" in str(row.get("metric", ""))
            for row in defense_event_rows
        )
        defense_active = (
            active_action_count > 0
            or defense_success
            or drop_rules_active
            or counters_stopped
        )

        # Message-loss counters for loss rate
        msg_rows = _rows("message_loss_counters")
        generated_total = _safe_val(msg_rows, "generated_total")
        gateway_received = sum(
            _safe_float(r.get("value"))
            for r in msg_rows
            if "received" in str(r.get("metric", "")) and "sensors." in str(r.get("metric", ""))
        )
        gateway_dropped = sum(
            _safe_float(r.get("value"))
            for r in msg_rows
            if "dropped" in str(r.get("metric", ""))
        )
        loss_rate = 0.0
        if gateway_received + gateway_dropped > 0:
            loss_rate = max(0.0, min(1.0, gateway_dropped / max(gateway_received + gateway_dropped, 1.0)))
        elif generated_total > 0:
            loss_rate = max(0.0, min(1.0, max(generated_total - gateway_received, 0.0) / generated_total))

        return {
            "schema_version": "llm-mtd-defender-snapshot-v1",
            "built_at": fetched_at,
            "controller_reachable": controller_reachable,
            "qos": {
                "sensor_to_edge_latency_ms": sensor_to_edge_latency_ms,
                "edge_to_cloud_latency_ms": edge_to_cloud_latency_ms,
                "loss_rate": loss_rate,
            },
            "overhead": {
                "controller_apply_ms": controller_apply_ms,
                "flow_rules_installed": flow_rules_installed,
                "flow_delete_commands": flow_delete_commands,
                "meters_added": meters_added,
                "controller_active_actions": active_action_count,
            },
            "controller": {
                "active_actions": active_actions_list,
                "switch_count": len(switches) if isinstance(switches, list) else 0,
                "connected_switches": sum(
                    1 for s in switches if isinstance(s, dict) and s.get("connected")
                ) if isinstance(switches, list) else 0,
            },
            "attack_events": attack_event_rows,
            "defense_events": defense_event_rows,
            "attack_active": attack_active,
            "attack_events_count": attack_events_count,
            "defense_active": defense_active,
            "defense_success": defense_success,
            "drop_rules_active": drop_rules_active,
            "counters_stopped": counters_stopped,
            "source_errors": source_errors,
        }

    # ------------------------------------------------------------------
    # Background state streaming
    # ------------------------------------------------------------------

    def stream_state(
        self,
        interval_seconds: float,
        callback: Callable[[Dict[str, Any]], None],
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Run a background polling loop, calling *callback* with each snapshot.

        This method blocks the calling thread until *stop_event* is set (or
        forever if *stop_event* is ``None``). Call from a daemon thread if
        you need non-blocking behaviour.

        Parameters
        ----------
        interval_seconds:
            How often to poll the environment state.
        callback:
            Callable that receives each ``build_defender_state_snapshot()``
            result dict. Exceptions raised by the callback are suppressed
            but printed to stderr.
        stop_event:
            Optional :class:`threading.Event`; the loop exits when it is set.
        """
        while stop_event is None or not stop_event.is_set():
            try:
                snapshot = self.build_defender_state_snapshot()
                callback(snapshot)
            except Exception as error:
                import sys
                print(f"[DefenderEnvController] stream_state callback error: {error}", file=sys.stderr)
            time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LLM_MTD_modular defender environment controller health-check."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print a combined Ryu + cloud_metrics status snapshot and exit.",
    )
    parser.add_argument(
        "--apply-action",
        metavar="JSON",
        default="",
        help="JSON payload to POST to Ryu /mtd/action, then exit.",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Print current Ryu controller state snapshot and exit.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream state snapshots for 10 seconds (demo).",
    )
    parser.add_argument("--ryu-action-url", default=RYU_ACTION_URL)
    parser.add_argument("--ryu-status-url", default=RYU_STATUS_URL)
    parser.add_argument("--ryu-metrics-url", default=RYU_METRICS_URL)
    parser.add_argument("--core-url", default=CORE_URL)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--no-docker-fallback",
        action="store_true",
        help="Disable Docker exec fallback for cloud_metrics.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    import sys

    args = _build_arg_parser().parse_args(argv)
    controller = DefenderEnvController(
        ryu_action_url=args.ryu_action_url,
        ryu_status_url=args.ryu_status_url,
        ryu_metrics_url=args.ryu_metrics_url,
        core_url=args.core_url,
        docker_fallback=not args.no_docker_fallback,
        timeout=args.timeout,
    )

    if args.snapshot:
        ryu_status = controller.get_ryu_status()
        json.dump(ryu_status, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if args.apply_action:
        try:
            payload = json.loads(args.apply_action)
        except json.JSONDecodeError as error:
            print(f"Invalid JSON: {error}", file=sys.stderr)
            return 1
        result = controller.apply_action(payload)
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0 if result.get("ok") else 1

    if args.stream:
        print("Streaming defender state for 10 seconds...", flush=True)
        stop = threading.Event()

        def _print_snapshot(snapshot: Dict[str, Any]) -> None:
            json.dump(snapshot, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            sys.stdout.flush()

        stream_thread = threading.Thread(
            target=controller.stream_state,
            args=(2.0, _print_snapshot, stop),
            daemon=True,
        )
        stream_thread.start()
        time.sleep(10.0)
        stop.set()
        return 0

    # Default: --status
    snapshot = controller.build_defender_state_snapshot()
    json.dump(snapshot, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
