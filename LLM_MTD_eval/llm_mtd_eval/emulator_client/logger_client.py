from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ..types import HTTPResult


class LoggerClient:
    def __init__(self, event_url: str, timeout_seconds: float = 3.0, retries: int = 2) -> None:
        self.event_url = event_url
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def post_event(self, payload: dict[str, Any]) -> HTTPResult:
        body = json.dumps(payload).encode("utf-8")
        last_error = ""
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                self.event_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    response_body = response.read().decode("utf-8", errors="replace")
                    parsed_json: dict[str, Any] | list[Any] | None = None
                    try:
                        parsed_json = json.loads(response_body or "{}")
                    except json.JSONDecodeError:
                        parsed_json = None
                    return HTTPResult(
                        ok=200 <= response.status < 300,
                        status_code=response.status,
                        url=self.event_url,
                        body_text=response_body,
                        parsed_json=parsed_json,
                    )
            except urllib.error.HTTPError as error:
                return HTTPResult(
                    ok=False,
                    status_code=error.code,
                    url=self.event_url,
                    body_text=error.read().decode("utf-8", errors="replace"),
                    error=str(error),
                )
            except Exception as error:
                last_error = str(error)
                if attempt < self.retries:
                    time.sleep(0.1 * (attempt + 1))
        return HTTPResult(ok=False, status_code=0, url=self.event_url, error=last_error)
