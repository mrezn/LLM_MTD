from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from eval.types import HTTPResult


class CloudPolicyClient:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0, retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def post_context(self, payload: dict[str, Any]) -> HTTPResult:
        url = self._endpoint("/context")
        return self._post_json(url, payload)

    def request_decision(
        self,
        payload: dict[str, Any] | None = None,
        use_get: bool = False,
    ) -> HTTPResult:
        url = self._endpoint("/decide")
        if use_get:
            return self._get_json(url)
        return self._post_json(url, payload or {})

    def _endpoint(self, suffix: str) -> str:
        if self.base_url.endswith(suffix):
            return self.base_url
        return f"{self.base_url}{suffix}"

    def _get_json(self, url: str) -> HTTPResult:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                return HTTPResult(
                    ok=200 <= response.status < 300,
                    status_code=response.status,
                    url=url,
                    body_text=body,
                    parsed_json=json.loads(body or "{}"),
                )
        except urllib.error.HTTPError as error:
            return HTTPResult(
                ok=False,
                status_code=error.code,
                url=url,
                body_text=error.read().decode("utf-8", errors="replace"),
                error=str(error),
            )
        except Exception as error:
            return HTTPResult(ok=False, status_code=0, url=url, error=str(error))

    def _post_json(self, url: str, payload: dict[str, Any]) -> HTTPResult:
        body = json.dumps(payload).encode("utf-8")
        last_error = ""
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    response_body = response.read().decode("utf-8", errors="replace")
                    parsed = json.loads(response_body or "{}")
                    return HTTPResult(
                        ok=200 <= response.status < 300,
                        status_code=response.status,
                        url=url,
                        body_text=response_body,
                        parsed_json=parsed,
                    )
            except urllib.error.HTTPError as error:
                return HTTPResult(
                    ok=False,
                    status_code=error.code,
                    url=url,
                    body_text=error.read().decode("utf-8", errors="replace"),
                    error=str(error),
                )
            except Exception as error:
                last_error = str(error)
                if attempt < self.retries:
                    time.sleep(0.1 * (attempt + 1))
        return HTTPResult(ok=False, status_code=0, url=url, error=last_error)
