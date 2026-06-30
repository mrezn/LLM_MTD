import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mtd_common import (
    env,
    env_float,
    env_int,
    now_iso,
    post_json,
    read_json,
    route_path,
    route_query,
    send_json,
)


LOG_PATH = env("LOG_PATH", "/data/experiment-events.jsonl")
METRICS_URL = env("METRICS_URL")
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 2.0)
PORT = env_int("PORT", 8000)

state_lock = threading.Lock()
state = {"events_total": 0, "attack_events_total": 0, "defense_events_total": 0}
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def append_event(event):
    event.setdefault("timestamp", now_iso())
    with state_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(event, sort_keys=True) + "\n")
        state["events_total"] += 1
        if is_attack_event(event):
            state["attack_events_total"] += 1
        if is_defense_event(event):
            state["defense_events_total"] += 1
    return event


def is_attack_event(event):
    return str(event.get("event_type", "")).startswith("attack_")


def is_defense_event(event):
    return str(event.get("event_type", "")) == "defense_result"


def is_experiment_event(event):
    return is_attack_event(event) or is_defense_event(event)


def report_experiment_event(event):
    if not METRICS_URL or not is_experiment_event(event):
        return
    post_json(METRICS_URL, event, timeout=HTTP_TIMEOUT_SECONDS)


def tail_events(limit):
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as log_file:
        lines = log_file.readlines()[-limit:]
    return [json.loads(line) for line in lines if line.strip()]


class LoggerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            with state_lock:
                payload = {
                    "service": "cloud-logger",
                    "log_path": LOG_PATH,
                    "metrics_url": METRICS_URL,
                    **state,
                }
            send_json(self, 200, payload)
            return

        if path == "/events":
            query = route_query(self)
            limit = int(query.get("limit", ["50"])[0])
            send_json(self, 200, {"events": tail_events(limit)})
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)
        if path not in ("/log", "/attack/event", "/experiment/event"):
            send_json(self, 404, {"error": "not found"})
            return

        event = read_json(self)
        if path == "/attack/event":
            event.setdefault("event_type", "attack_event")
        if path == "/experiment/event":
            event.setdefault("event_type", "experiment_event")
        append_event(event)
        report_experiment_event(event)
        send_json(self, 202, {"status": "logged"})


def main():
    print(f"cloud-logger starting log_path={LOG_PATH} port={PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), LoggerHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
