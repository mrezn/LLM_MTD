import time

import requests


class OllamaClient:
    def __init__(self, host, timeout_s):
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s

    def generate(self, model, prompt):
        payload = {"model": model, "prompt": prompt, "stream": False}
        start = time.time()
        try:
            resp = requests.post(
                f"{self.host}/api/generate", json=payload, timeout=self.timeout_s
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", ""), time.time() - start
        except Exception:
            return "", time.time() - start
