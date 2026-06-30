"""Serve Dashboard.html and proxy the live metrics endpoints.

Run from the project root:

    python3 dashboard_server.py

If the host cannot reach the Containernet cloud_metrics IP directly, run with
Docker access so the fallback can execute inside mn.cloud_metrics:

    sudo -E python3 dashboard_server.py
"""

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DASHBOARD_FILE = ROOT / "Dashboard.html"
STRATEGY_DIR = Path(__file__).resolve().parents[1] / "game"
DECISION_TRACE_FILE = Path(
    os.environ.get("DASHBOARD_DECISION_TRACE_FILE", str(STRATEGY_DIR / "decision_trace.jsonl"))
)
EVAL_DECISION_TRACE_FILE = Path(
    os.environ.get(
        "DASHBOARD_EVAL_DECISION_TRACE_FILE",
        str(Path(__file__).resolve().parents[1] / "outputs" / "raw" / "live_decision_trace.jsonl"),
    )
)
STAGE_HISTORY_FILE = Path(
    os.environ.get("DASHBOARD_STAGE_HISTORY_FILE", str(STRATEGY_DIR / "stage_history.jsonl"))
)
EVAL_SUMMARY_FILE = Path(
    os.environ.get("DASHBOARD_EVAL_SUMMARY_FILE",
                    str(Path(__file__).resolve().parents[1] / "outputs" / "raw" / "stage_summaries.jsonl"))
)

LISTEN_HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("DASHBOARD_PORT", "8088"))
CONFIGURED_CLOUD_METRICS_URL = os.environ.get("DASHBOARD_CLOUD_METRICS_URL", "").strip().rstrip("/")
DEFAULT_CLOUD_METRICS_URL = os.environ.get(
    "DASHBOARD_DEFAULT_CLOUD_METRICS_URL",
    "http://10.0.10.12:8000",
).rstrip("/")
DASHBOARD_DISCOVERY_PORT_START = int(os.environ.get("DASHBOARD_DISCOVERY_PORT_START", "32790"))
DASHBOARD_DISCOVERY_PORT_END = int(os.environ.get("DASHBOARD_DISCOVERY_PORT_END", "32999"))
RYU_URL = os.environ.get("DASHBOARD_RYU_URL", "http://127.0.0.1:8080").rstrip("/")
DOCKER_FALLBACK = os.environ.get("DASHBOARD_DOCKER_FALLBACK", "1") != "0"
CLOUD_METRICS_CONTAINER = os.environ.get(
    "DASHBOARD_CLOUD_METRICS_CONTAINER",
    "mn.cloud_metrics",
)
DISCOVERED_CLOUD_METRICS_URL = ""


def fetch_http(url, timeout=3):
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return (
            response.status,
            response.headers.get("Content-Type", "application/octet-stream"),
            response.read(),
        )


def parse_json_bytes(body):
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {}


def read_jsonl_tail(path, limit=30):
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def infer_stage_scenario_id(row):
    if not isinstance(row, dict):
        return ""
    candidates = [
        row.get("scenario_id"),
        (row.get("state_summary") or {}).get("scenario_id"),
        (row.get("next_state") or {}).get("scenario_id"),
        (row.get("previous_state") or {}).get("scenario_id"),
        ((row.get("selection") or {}).get("attacker") or {}).get("scenario_id"),
        ((row.get("selection") or {}).get("defender") or {}).get("scenario_id"),
    ]
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized and normalized != "None":
            return normalized
    return ""


def normalize_stage_history_row(row):
    if not isinstance(row, dict):
        return {}

    normalized = dict(row)
    game = normalized.get("game") if isinstance(normalized.get("game"), dict) else {}
    game = dict(game)
    population_before = normalized.get("population_before") if isinstance(normalized.get("population_before"), dict) else {}
    population_after = normalized.get("population_after") if isinstance(normalized.get("population_after"), dict) else {}

    if "attacker_population" not in game and isinstance(population_after.get("attacker"), dict):
        game["attacker_population"] = population_after["attacker"]
    if "defender_population" not in game and isinstance(population_after.get("defender"), dict):
        game["defender_population"] = population_after["defender"]
    if "attacker_population_before" not in game and isinstance(population_before.get("attacker"), dict):
        game["attacker_population_before"] = population_before["attacker"]
    if "defender_population_before" not in game and isinstance(population_before.get("defender"), dict):
        game["defender_population_before"] = population_before["defender"]

    normalized["game"] = game
    normalized["scenario_id"] = infer_stage_scenario_id(normalized)

    stage_summary = normalized.get("stage_summary")
    if not isinstance(stage_summary, dict):
        stage_summary = {}
    else:
        stage_summary = dict(stage_summary)
    if not stage_summary.get("summary_text") and normalized.get("summary_text"):
        stage_summary["summary_text"] = normalized.get("summary_text")
    for key in ("security_outcome", "qos_delta", "controller_delta", "stage_validation"):
        if key not in stage_summary and isinstance(normalized.get(key), dict):
            stage_summary[key] = normalized[key]
    if stage_summary:
        normalized["stage_summary"] = stage_summary
    return normalized


def send_json(handler, payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def is_cloud_metrics_response(body):
    parsed = parse_json_bytes(body)
    return isinstance(parsed, dict) and parsed.get("service") == "cloud-metrics"


def core_response_looks_valid(body):
    parsed = parse_json_bytes(body)
    return isinstance(parsed, dict) and "attack_events" in parsed and "message_loss_counters" in parsed


def local_port_open(port, timeout=0.1):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def candidate_cloud_metrics_urls():
    yielded = set()
    for candidate in (DISCOVERED_CLOUD_METRICS_URL, CONFIGURED_CLOUD_METRICS_URL):
        if candidate and candidate not in yielded:
            yielded.add(candidate)
            yield candidate
    for port in range(DASHBOARD_DISCOVERY_PORT_START, DASHBOARD_DISCOVERY_PORT_END + 1):
        if not local_port_open(port):
            continue
        candidate = f"http://127.0.0.1:{port}"
        if candidate in yielded:
            continue
        yielded.add(candidate)
        yield candidate
    if DEFAULT_CLOUD_METRICS_URL and DEFAULT_CLOUD_METRICS_URL not in yielded:
        yield DEFAULT_CLOUD_METRICS_URL


def discover_cloud_metrics_url(force_refresh=False):
    global DISCOVERED_CLOUD_METRICS_URL
    if DISCOVERED_CLOUD_METRICS_URL and not force_refresh:
        return DISCOVERED_CLOUD_METRICS_URL

    for base_url in candidate_cloud_metrics_urls():
        try:
            status, _, body = fetch_http(f"{base_url}/health", timeout=0.5)
            if status == 200 and is_cloud_metrics_response(body):
                DISCOVERED_CLOUD_METRICS_URL = base_url.rstrip("/")
                return DISCOVERED_CLOUD_METRICS_URL
        except Exception:
            pass

        try:
            status, _, body = fetch_http(f"{base_url}/core", timeout=0.5)
            if status == 200 and core_response_looks_valid(body):
                DISCOVERED_CLOUD_METRICS_URL = base_url.rstrip("/")
                return DISCOVERED_CLOUD_METRICS_URL
        except Exception:
            pass

    DISCOVERED_CLOUD_METRICS_URL = (CONFIGURED_CLOUD_METRICS_URL or DEFAULT_CLOUD_METRICS_URL).rstrip("/")
    return DISCOVERED_CLOUD_METRICS_URL


def fetch_from_cloud_metrics_container(path):
    if not DOCKER_FALLBACK:
        raise RuntimeError("Docker fallback disabled")

    container_url = f"http://127.0.0.1:8000{path}"
    python_code = (
        "import sys, urllib.request\n"
        "with urllib.request.urlopen(sys.argv[1], timeout=3) as r:\n"
        "    sys.stdout.buffer.write(r.read())\n"
    )
    commands = [
        ["docker", "exec", CLOUD_METRICS_CONTAINER, "python3", "-c", python_code, container_url],
        ["docker", "exec", CLOUD_METRICS_CONTAINER, "python", "-c", python_code, container_url],
        ["docker", "exec", CLOUD_METRICS_CONTAINER, "curl", "-fsS", container_url],
        ["docker", "exec", CLOUD_METRICS_CONTAINER, "wget", "-qO-", container_url],
    ]

    errors = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=6,
            )
        except Exception as error:
            errors.append(f"{command[3]} failed to start: {error}")
            continue

        if result.returncode == 0:
            content_type = (
                "application/json"
                if path in ("/core", "/experiment/summary")
                else "text/plain"
            )
            return 200, content_type, result.stdout

        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        errors.append(f"{command[3]}: {detail}")

    raise RuntimeError(
        f"Docker fallback could not query {CLOUD_METRICS_CONTAINER}. "
        "Run this server with Docker access, for example `sudo -E python3 dashboard_server.py`, "
        "or set DASHBOARD_CLOUD_METRICS_URL to a host-reachable cloud_metrics URL. "
        f"Attempts: {' | '.join(errors)}"
    )


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("", "/", "/Dashboard.html"):
            self.send_file(DASHBOARD_FILE, "text/html; charset=utf-8")
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path in ("/core", "/experiment/summary", "/metrics"):
            self.proxy_cloud_metrics(path)
            return

        if path.startswith("/mtd/"):
            self.proxy_http(f"{RYU_URL}{path}", "Ryu")
            return

        if path == "/decision-trace":
            source = urllib.parse.parse_qs(parsed.query).get("source", ["live"])[0]
            if source == "eval":
                rows = read_jsonl_tail(EVAL_DECISION_TRACE_FILE, 40)
            elif source == "both":
                rows = read_jsonl_tail(DECISION_TRACE_FILE, 20) + read_jsonl_tail(EVAL_DECISION_TRACE_FILE, 20)
            else:
                rows = read_jsonl_tail(DECISION_TRACE_FILE, 40)
            send_json(self, {
                "rows": rows,
                "source": source,
                "live_path": str(DECISION_TRACE_FILE),
                "eval_path": str(EVAL_DECISION_TRACE_FILE),
            })
            return

        if path == "/stage-history":
            rows = [normalize_stage_history_row(row) for row in read_jsonl_tail(STAGE_HISTORY_FILE, 40)]
            send_json(self, {"rows": rows})
            return

        if path == "/eval-summary":
            rows = [normalize_stage_history_row(row) for row in read_jsonl_tail(EVAL_SUMMARY_FILE, 40)]
            send_json(self, {
                "rows": rows,
                "exists": EVAL_SUMMARY_FILE.exists(),
                "expected_path": str(EVAL_SUMMARY_FILE),
                "message": (
                    ""
                    if EVAL_SUMMARY_FILE.exists()
                    else f"No eval summary file found yet at {EVAL_SUMMARY_FILE}"
                ),
            })
            return

        self.send_error(404, "File not found")

    def send_file(self, file_path, content_type):
        if not file_path.exists():
            self.send_error(404, "File not found")
            return

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def proxy_cloud_metrics(self, path):
        base_url = discover_cloud_metrics_url()
        target = f"{base_url}{path}"
        try:
            status, content_type, body = fetch_http(target)
        except Exception as direct_error:
            refreshed_url = discover_cloud_metrics_url(force_refresh=True)
            if refreshed_url and refreshed_url != base_url:
                try:
                    status, content_type, body = fetch_http(f"{refreshed_url}{path}")
                    self.send_proxy_response(status, content_type, body)
                    return
                except Exception as refreshed_error:
                    direct_error = f"{direct_error}; rediscovery={refreshed_error}"
            try:
                status, content_type, body = fetch_from_cloud_metrics_container(path)
            except Exception as fallback_error:
                message = (
                    "Could not reach cloud_metrics through "
                    f"{target} or Docker fallback. direct={direct_error}; "
                    f"fallback={fallback_error}"
                )
                self.send_text(502, message)
                return

        self.send_proxy_response(status, content_type, body)

    def proxy_http(self, target, label):
        try:
            status, content_type, body = fetch_http(target)
        except urllib.error.HTTPError as error:
            self.send_proxy_response(
                error.code,
                error.headers.get("Content-Type", "text/plain"),
                error.read(),
            )
            return
        except Exception as error:
            self.send_text(502, f"Could not reach {label} endpoint {target}: {error}")
            return

        self.send_proxy_response(status, content_type, body)

    def send_proxy_response(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status, message):
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    resolved_cloud_metrics_url = discover_cloud_metrics_url(force_refresh=True)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), DashboardHandler)
    print(
        f"Dashboard server: http://{LISTEN_HOST}:{LISTEN_PORT}/Dashboard.html\n"
        f"  /core and /metrics -> {resolved_cloud_metrics_url}, Docker fallback={DOCKER_FALLBACK}\n"
        f"  Docker fallback container -> {CLOUD_METRICS_CONTAINER}\n"
        f"  /mtd/* -> {RYU_URL}/mtd/*",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
