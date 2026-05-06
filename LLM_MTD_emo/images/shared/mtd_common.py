import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def env(name, default=""):
    return os.environ.get(name, default)


def env_int(name, default):
    raw_value = os.environ.get(name)
    return default if raw_value in (None, "") else int(raw_value)


def env_float(name, default):
    raw_value = os.environ.get(name)
    return default if raw_value in (None, "") else float(raw_value)


def env_csv(name, default=None):
    raw_value = os.environ.get(name)
    if raw_value in (None, ""):
        return list(default or [])
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def env_json(name, default=None):
    raw_value = os.environ.get(name)
    if raw_value in (None, ""):
        return default
    return json.loads(raw_value)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def elapsed_ms_between(start_iso, end_iso=None):
    start = parse_iso_timestamp(start_iso)
    end = parse_iso_timestamp(end_iso) if end_iso else datetime.now(timezone.utc)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000


def rate_per_second(count, started_at):
    elapsed = time.monotonic() - started_at
    if elapsed <= 0:
        return 0.0
    return count / elapsed


def json_size(payload):
    return len(json.dumps(payload).encode("utf-8"))


def route_path(handler):
    return urllib.parse.urlparse(handler.path).path


def route_query(handler):
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)


def read_request_body(handler):
    content_length = int(handler.headers.get("Content-Length", "0") or 0)
    return handler.rfile.read(content_length) if content_length else b""


def read_json(handler):
    body = read_request_body(handler)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def read_json_with_size(handler):
    body = read_request_body(handler)
    if not body:
        return {}, 0
    return json.loads(body.decode("utf-8")), len(body)


def send_json(handler, status, payload):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_text(handler, status, payload, content_type="text/plain; version=0.0.4"):
    body = payload.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def post_json(url, payload, timeout=2.0):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started_at = time.monotonic()

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.monotonic() - started_at) * 1000
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "body": response_body,
                "elapsed_ms": elapsed_ms,
                "request_bytes": len(body),
                "response_bytes": len(response_body.encode("utf-8")),
                "error": "",
            }
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.monotonic() - started_at) * 1000
        return {
            "ok": False,
            "status": error.code,
            "body": response_body,
            "elapsed_ms": elapsed_ms,
            "request_bytes": len(body),
            "response_bytes": len(response_body.encode("utf-8")),
            "error": str(error),
        }
    except Exception as error:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        return {
            "ok": False,
            "status": 0,
            "body": "",
            "elapsed_ms": elapsed_ms,
            "request_bytes": len(body),
            "response_bytes": 0,
            "error": str(error),
        }


def metric_line(name, value, labels=None):
    label_text = ""
    if labels:
        rendered_labels = [
            f'{key}="{escape_label_value(value)}"' for key, value in labels.items()
        ]
        label_text = "{" + ",".join(rendered_labels) + "}"
    return f"{name}{label_text} {value}"


def escape_label_value(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
