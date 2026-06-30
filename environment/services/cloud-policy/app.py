import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import resource
except ImportError:
    resource = None

from mtd_common import (
    env,
    env_float,
    env_int,
    metric_line,
    now_iso,
    post_json,
    read_json,
    route_path,
    send_json,
    send_text,
)


POLICY_MODE = env("POLICY_MODE", "game-baseline")
LLM_POLICY_PROVIDER = env("LLM_POLICY_PROVIDER", "disabled")
DROP_THRESHOLD = env_float("DROP_THRESHOLD", 5.0)
QUEUE_THRESHOLD = env_float("QUEUE_THRESHOLD", 50.0)
LATENCY_THRESHOLD_MS = env_float("LATENCY_THRESHOLD_MS", 250.0)
METRICS_URL = env("METRICS_URL")
LOGGER_URL = env("LOGGER_URL")
REPORT_INTERVAL_SECONDS = env_float("REPORT_INTERVAL_SECONDS", 5.0)
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 2.0)
PORT = env_int("PORT", 8000)
POLICY_OBSERVE_ONLY = env("POLICY_OBSERVE_ONLY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
MULVAL_POLICY_JSON = env("MULVAL_POLICY_JSON", "/data/mulval_policy.json")
CALDERA_RESULT_JSON = env("CALDERA_RESULT_JSON", "/data/caldera_result.json")

started_at = time.monotonic()
state_lock = threading.RLock()
state = {
    "decisions_total": 0,
    "last_decision": {},
    "external_context": {},
    "external_context_updated_at": "",
}


def memory_kb():
    if resource is None:
        return 0
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def snapshot():
    with state_lock:
        return {
            "decisions_total": state["decisions_total"],
            "uptime_seconds": time.monotonic() - started_at,
            "cpu_seconds": time.process_time(),
            "memory_kb": memory_kb(),
        }


def report_metrics():
    if not METRICS_URL:
        return
    post_json(
        METRICS_URL,
        {
            "source": "cloud_policy",
            "role": "cloud_policy",
            "timestamp": now_iso(),
            "metrics": snapshot(),
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )


def metrics_report_loop():
    while True:
        time.sleep(REPORT_INTERVAL_SECONDS)
        report_metrics()


def log_event(event):
    if not LOGGER_URL:
        return
    post_json(LOGGER_URL, event, timeout=HTTP_TIMEOUT_SECONDS)


def read_optional_json_file(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as json_file:
            return json.load(json_file)
    except (OSError, json.JSONDecodeError):
        return None


def normalize_caldera_result(payload):
    if not isinstance(payload, dict):
        return payload
    if "caldera_result" in payload:
        return payload.get("caldera_result")
    return payload


def normalize_defense_result(payload):
    if isinstance(payload, dict):
        return payload.get("defense_result")
    return None


def policy_context_snapshot():
    context = {}

    mulval_policy = read_optional_json_file(MULVAL_POLICY_JSON)
    if mulval_policy is not None:
        context["mulval_policy"] = mulval_policy

    caldera_payload = read_optional_json_file(CALDERA_RESULT_JSON)
    if caldera_payload is not None:
        context["caldera_result"] = normalize_caldera_result(caldera_payload)
        defense_result = normalize_defense_result(caldera_payload)
        if defense_result is not None:
            context["defense_result"] = defense_result

    with state_lock:
        context.update(state.get("external_context", {}))
        context["external_context_updated_at"] = state.get("external_context_updated_at", "")

    return context


def update_external_context(payload):
    accepted = {}
    for key in (
        "mulval_policy",
        "caldera_result",
        "defense_result",
        "strategy_layer",
        "selected_attacker_strategy",
        "selected_defender_strategy",
    ):
        if key in payload:
            accepted[key] = payload[key]

    if not accepted and "operation_id" in payload and "scenario_id" in payload:
        accepted["caldera_result"] = payload

    with state_lock:
        state["external_context"].update(accepted)
        state["external_context_updated_at"] = now_iso()

    return accepted


def selected_strategy_defender_action(context):
    strategy = None
    if isinstance(context, dict):
        strategy = context.get("selected_defender_strategy")
        strategy_layer = context.get("strategy_layer")
        if strategy is None and isinstance(strategy_layer, dict):
            selection = strategy_layer.get("selection", {})
            if isinstance(selection, dict):
                strategy = selection.get("defender")

    if not isinstance(strategy, dict):
        return None

    payload = strategy.get("action_payload")
    if isinstance(payload, dict) and payload.get("action"):
        return dict(payload)

    action = strategy.get("action")
    if not action:
        return None

    result = {"action": action}
    if strategy.get("target"):
        result["target"] = strategy["target"]
    return result


def flatten_metrics(value, path=""):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            nested_path = f"{path}.{key}" if path else str(key)
            yield from flatten_metrics(nested_value, nested_path)
    elif isinstance(value, (int, float)):
        yield path, float(value)


def max_metric(metrics, token):
    values = [value for name, value in flatten_metrics(metrics) if token in name]
    return max(values) if values else 0.0


def summarize_external_context(context):
    mulval_policy = context.get("mulval_policy") if isinstance(context, dict) else {}
    caldera_result = context.get("caldera_result") if isinstance(context, dict) else {}
    defense_result = context.get("defense_result") if isinstance(context, dict) else {}

    mulval_paths = []
    if isinstance(mulval_policy, dict):
        mulval_paths = mulval_policy.get("attack_paths") or []

    attempted_path = []
    caldera_success = None
    operation_id = ""
    scenario_id = ""
    entry_node = ""
    if isinstance(caldera_result, dict):
        attempted_path = caldera_result.get("attempted_path") or caldera_result.get("mulval_path") or []
        caldera_success = caldera_result.get("success")
        operation_id = caldera_result.get("operation_id", "")
        scenario_id = caldera_result.get("scenario_id", "")
        entry_node = caldera_result.get("entry_node", "")

    defense_success = None
    defense_action = ""
    if isinstance(defense_result, dict):
        defense_success = defense_result.get("defense_success", defense_result.get("success"))
        defense_action = defense_result.get("defense_action", defense_result.get("action", ""))

    return {
        "mulval_path_count": len(mulval_paths),
        "first_mulval_path": mulval_paths[0] if mulval_paths else [],
        "operation_id": operation_id,
        "scenario_id": scenario_id,
        "entry_node": entry_node,
        "attempted_path": attempted_path,
        "caldera_success": caldera_success,
        "defense_action": defense_action,
        "defense_success": defense_success,
    }


def choose_defense_action(context):
    external_summary = summarize_external_context(context)
    target = (
        context.get("target")
        or context.get("sensor_id")
        or external_summary.get("entry_node")
        or "unknown"
    )
    metrics = context.get("metrics", context)
    max_drops = max(max_metric(metrics, "dropped"), max_metric(metrics, "drop"))
    max_queue = max_metric(metrics, "queue")
    max_latency = max(max_metric(metrics, "latency"), max_metric(metrics, "delay"))

    has_external_context = bool(
        context.get("mulval_policy") or context.get("caldera_result") or context.get("defense_result")
    )
    strategy_action = selected_strategy_defender_action(context)

    if strategy_action:
        action_type = strategy_action.get("action", "observe")
        target = strategy_action.get("target", target)
        reason = "strategy layer selected defender action"
    elif POLICY_OBSERVE_ONLY and has_external_context:
        action_type = "observe"
        reason = "observe-only MulVAL/Caldera context loaded"
    elif max_drops >= DROP_THRESHOLD:
        action_type = "isolate_sensor"
        reason = "drop pressure crossed policy threshold"
    elif max_queue >= QUEUE_THRESHOLD:
        action_type = "rate_limit_sensor"
        reason = "edge queue pressure crossed policy threshold"
    elif max_latency >= LATENCY_THRESHOLD_MS:
        action_type = "reroute_sensor"
        reason = "forwarding or processing latency crossed policy threshold"
    else:
        action_type = "observe"
        reason = "system metrics are within baseline thresholds"

    return {
        "policy_mode": POLICY_MODE,
        "llm_policy_provider": LLM_POLICY_PROVIDER,
        "observe_only": POLICY_OBSERVE_ONLY,
        "selected_at": now_iso(),
        "selected_action": {
            "type": action_type,
            "target": target,
            "reason": reason,
            "enforcement_owner": "ryu",
            "ovs_direct_manipulation": False,
            "ryu_intent": {
                **(strategy_action or {}),
                "action": action_type,
                "target": target,
                "source": "cloud_policy",
            },
        },
        "scores": {
            "max_drops": max_drops,
            "max_queue": max_queue,
            "max_latency_ms": max_latency,
        },
        "external_context": external_summary,
    }


class PolicyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            with state_lock:
                payload = {
                    "service": "cloud-policy",
                    "policy_mode": POLICY_MODE,
                    "llm_policy_provider": LLM_POLICY_PROVIDER,
                    "observe_only": POLICY_OBSERVE_ONLY,
                    "thresholds": {
                        "drop": DROP_THRESHOLD,
                        "queue": QUEUE_THRESHOLD,
                        "latency_ms": LATENCY_THRESHOLD_MS,
                    },
                    "metrics_url": METRICS_URL,
                    "logger_url": LOGGER_URL,
                    "mulval_policy_json": MULVAL_POLICY_JSON,
                    "caldera_result_json": CALDERA_RESULT_JSON,
                    "loaded_external_context": policy_context_snapshot(),
                    "stats": snapshot(),
                    **state,
                }
            send_json(self, 200, payload)
            return

        if path == "/context":
            send_json(self, 200, policy_context_snapshot())
            return

        if path == "/metrics":
            values = snapshot()
            labels = {"service": "cloud_policy"}
            lines = [
                metric_line("cloud_policy_decisions_total", values["decisions_total"], labels),
                metric_line("cloud_policy_cpu_seconds", values["cpu_seconds"], labels),
                metric_line("cloud_policy_memory_kb", values["memory_kb"], labels),
            ]
            send_text(self, 200, "\n".join(lines) + "\n")
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)
        if path == "/context":
            payload = read_json(self)
            accepted = update_external_context(payload)
            send_json(
                self,
                202,
                {
                    "status": "accepted",
                    "accepted_keys": sorted(accepted.keys()),
                    "observe_only": POLICY_OBSERVE_ONLY,
                },
            )
            return

        if path != "/decide":
            send_json(self, 404, {"error": "not found"})
            return

        context = read_json(self)
        merged_context = policy_context_snapshot()
        merged_context.update(context)
        decision = choose_defense_action(merged_context)
        with state_lock:
            state["decisions_total"] += 1
            state["last_decision"] = decision
        report_metrics()
        log_event(
            {
                "event_type": "cloud_policy_decision",
                "source": "cloud_policy",
                "timestamp": decision["selected_at"],
                "decision": decision,
            }
        )
        send_json(self, 200, decision)


def main():
    print(
        f"cloud-policy starting policy_mode={POLICY_MODE} llm_provider={LLM_POLICY_PROVIDER} port={PORT}",
        flush=True,
    )
    reporter_thread = threading.Thread(target=metrics_report_loop, daemon=True)
    reporter_thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), PolicyHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
