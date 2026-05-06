from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ..logging_utils import get_logger
from ..types import HTTPResult


LOGGER = get_logger(__name__)


class CoreClient:
    def __init__(
        self,
        core_url: str,
        experiment_summary_url: str | None = None,
        timeout_seconds: float = 3.0,
        retries: int = 2,
    ) -> None:
        self.core_url = core_url
        self.experiment_summary_url = experiment_summary_url or core_url.replace(
            "/core",
            "/experiment/summary",
        )
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def get_core(self) -> dict[str, Any]:
        result = self._get_json(self.core_url)
        return result.parsed_json if isinstance(result.parsed_json, dict) else {}

    def get_experiment_summary(self) -> dict[str, Any]:
        result = self._get_json(self.experiment_summary_url)
        return result.parsed_json if isinstance(result.parsed_json, dict) else {}

    def _get_json(self, url: str) -> HTTPResult:
        last_error = ""
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    parsed = json.loads(body or "{}")
                    return HTTPResult(
                        ok=200 <= response.status < 300,
                        status_code=response.status,
                        url=url,
                        body_text=body,
                        parsed_json=parsed,
                    )
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                last_error = str(error)
                LOGGER.warning("CoreClient HTTP error url=%s status=%s", url, error.code)
                return HTTPResult(
                    ok=False,
                    status_code=error.code,
                    url=url,
                    body_text=body,
                    error=last_error,
                )
            except Exception as error:
                last_error = str(error)
                if attempt < self.retries:
                    time.sleep(0.1 * (attempt + 1))
        return HTTPResult(
            ok=False,
            status_code=0,
            url=url,
            error=last_error,
        )
