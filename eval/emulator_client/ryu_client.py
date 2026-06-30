from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from eval.types import HTTPResult


class RyuClient:
    def __init__(
        self,
        status_url: str,
        metrics_url: str,
        action_url: str,
        timeout_seconds: float = 3.0,
        retries: int = 2,
    ) -> None:
        self.status_url = status_url
        self.metrics_url = metrics_url
        self.action_url = action_url
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def get_status(self) -> dict[str, Any]:
        result = self._get_json(self.status_url)
        return result.parsed_json if isinstance(result.parsed_json, dict) else {}

    def get_metrics(self) -> str:
        result = self._get_text(self.metrics_url)
        return result.body_text if result.ok else ""

    def apply_action(self, payload: dict[str, Any]) -> HTTPResult:
        return self._post_json(self.action_url, payload)

    def _get_json(self, url: str) -> HTTPResult:
        text_result = self._get_text(url)
        if not text_result.ok:
            return text_result
        try:
            parsed = json.loads(text_result.body_text or "{}")
        except json.JSONDecodeError as error:
            return HTTPResult(
                ok=False,
                status_code=text_result.status_code,
                url=url,
                body_text=text_result.body_text,
                error=str(error),
            )
        return text_result.model_copy(update={"parsed_json": parsed})

    def _get_text(self, url: str) -> HTTPResult:
        last_error = ""
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return HTTPResult(
                        ok=200 <= response.status < 300,
                        status_code=response.status,
                        url=url,
                        body_text=response.read().decode("utf-8", errors="replace"),
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
                    parsed_json: dict[str, Any] | list[Any] | None = None
                    try:
                        parsed_json = json.loads(response_body or "{}")
                    except json.JSONDecodeError:
                        parsed_json = None
                    return HTTPResult(
                        ok=200 <= response.status < 300,
                        status_code=response.status,
                        url=url,
                        body_text=response_body,
                        parsed_json=parsed_json,
                    )
            except urllib.error.HTTPError as error:
                response_body = error.read().decode("utf-8", errors="replace")
                parsed_json = None
                try:
                    parsed_json = json.loads(response_body or "{}")
                except json.JSONDecodeError:
                    parsed_json = None
                return HTTPResult(
                    ok=False,
                    status_code=error.code,
                    url=url,
                    body_text=response_body,
                    parsed_json=parsed_json,
                    error=str(error),
                )
            except Exception as error:
                last_error = str(error)
                if attempt < self.retries:
                    time.sleep(0.1 * (attempt + 1))
        return HTTPResult(ok=False, status_code=0, url=url, error=last_error)
