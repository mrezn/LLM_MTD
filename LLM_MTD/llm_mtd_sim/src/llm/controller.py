import numpy as np

from .ollama_client import OllamaClient
from .prompts import build_macro_prompt, build_summary_prompt
from .schema import parse_macro_output, fallback
from ..utils import normalize


class LLMController:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = OllamaClient(cfg["llm"]["ollama_host"], cfg["llm"]["llm_timeout_s"])
        self.macro_model = cfg["llm"]["llm_macro_model"]
        self.summary_model = cfg["llm"]["llm_summary_model"]

    def macro_decision(self, context, active_keys, pool_keys, q):
        prompt = build_macro_prompt(context)
        text, latency = self.client.generate(self.macro_model, prompt)
        if not text:
            data = fallback(active_keys, q)
        else:
            data, valid = parse_macro_output(text, active_keys, pool_keys, q)
            if not valid:
                data = fallback(active_keys, q)

        sigma = [data["macro_probs"][k] for k in active_keys]
        sigma = normalize(np.array(sigma, dtype=float)).tolist()
        mutation = data.get("mutation", np.eye(len(active_keys)).tolist())

        return {
            "sigma": sigma,
            "mutation": mutation,
            "promote_key": data.get("promote_key", "NONE"),
            "demote_keys": data.get("demote_keys", []),
            "notes": data.get("notes", ""),
            "latency_s": float(latency),
        }

    def episode_summary(self, context):
        prompt = build_summary_prompt(context)
        text, latency = self.client.generate(self.summary_model, prompt)
        if not text:
            text = "Summary unavailable; Ollama not responding."
        return text.strip(), float(latency)
