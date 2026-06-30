from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from eval.types import LLMResponseTrace, NormalizedState


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


class LLMClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.provider = str(config.get("provider", "mock"))
        self.model_name = str(config.get("model_name", "mock-defender-v1"))
        self.timeout_seconds = float(config.get("timeout_seconds", 10.0))
        self.max_retries = int(config.get("max_retries", 2))
        self.temperature = float(config.get("temperature", 0.0))
        self.base_url = str(config.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
        self.strict_json = _as_bool(config.get("strict_json", False))
        self.keep_alive = str(config.get("ollama_keep_alive", config.get("keep_alive", "30m")))

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        state: NormalizedState | None = None,
    ) -> LLMResponseTrace:
        started_at = time.monotonic()
        if self.provider == "mock":
            raw_text = json.dumps(self._mock_decision(state), sort_keys=True)
            return LLMResponseTrace(
                provider=self.provider,
                model_name=self.model_name,
                raw_text=raw_text,
                latency_ms=(time.monotonic() - started_at) * 1000,
                retries_used=0,
                prompt_preview=user_prompt[:240],
            )

        if self.provider == "ollama":
            prompt = f"{system_prompt}\n\n{user_prompt}"
            last_error = ""
            for attempt in range(self.max_retries + 1):
                try:
                    payload = {
                        "model": self.model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": self.temperature},
                        "keep_alive": self.keep_alive,
                    }
                    if self.strict_json:
                        payload["format"] = "json"
                    body = json.dumps(payload).encode("utf-8")
                    request = urllib.request.Request(
                        f"{self.base_url}/api/generate",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                        response_body = response.read().decode("utf-8", errors="replace")
                        parsed = json.loads(response_body or "{}")
                        return LLMResponseTrace(
                            provider=self.provider,
                            model_name=self.model_name,
                            raw_text=str(parsed.get("response", "")),
                            latency_ms=(time.monotonic() - started_at) * 1000,
                            retries_used=attempt,
                            prompt_preview=user_prompt[:240],
                        )
                except urllib.error.HTTPError as error:
                    last_error = str(error)
                    if attempt >= self.max_retries:
                        break
                except Exception as error:
                    last_error = str(error)
                    if attempt >= self.max_retries:
                        break
            raise RuntimeError(f"Ollama completion failed: {last_error}")

        raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _mock_decision(self, state: NormalizedState | None) -> dict[str, Any]:
        if state is None:
            return {
                "selected_defender_strategy": "observe",
                "target": "",
                "parameters": {},
                "confidence": 0.5,
                "reasoning_summary": "No state was provided, so observe is the safe fallback.",
                "expected_security_gain": 0.1,
                "expected_qos_impact": 0.0,
                "active_pool_update": {"enabled": False, "promote": [], "demote": []},
            }

        security = state.security_context
        qos = state.qos_context
        if security.cloud_seen or security.attack_effect_success:
            action = "quarantine_sensor"
            target = state.entry_node
            parameters: dict[str, Any] = {}
            reason = "Attack indicators reached a high-severity stage, so isolation is preferred."
            security_gain = 0.82
            qos_impact = 0.62
        elif qos.queue_length >= 5 or qos.message_loss_rate > 0.05:
            action = "rate_limit"
            target = state.entry_node
            parameters = {"kbps": 128}
            reason = "Queue or loss pressure suggests throttling traffic before full isolation."
            security_gain = 0.58
            qos_impact = 0.32
        elif state.entry_node == "sen6":
            action = "reroute_traffic"
            target = "sen6"
            parameters = {"via": "s_edge2"}
            reason = "The dual-homed sensor can be steered toward the preferred edge path."
            security_gain = 0.44
            qos_impact = 0.18
        else:
            action = "observe"
            target = ""
            parameters = {}
            reason = "Current indicators do not justify an active intervention yet."
            security_gain = 0.12
            qos_impact = 0.03

        return {
            "selected_defender_strategy": action,
            "target": target,
            "parameters": parameters,
            "confidence": 0.78,
            "reasoning_summary": reason,
            "expected_security_gain": security_gain,
            "expected_qos_impact": qos_impact,
            "active_pool_update": {"enabled": False, "promote": [], "demote": []},
        }
