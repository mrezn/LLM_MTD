from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate

from ..types import LLMDecision


def extract_first_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Could not find a JSON object in the LLM response")
    return text[start : end + 1]


class ResponseParser:
    def __init__(self, schema_path: Path) -> None:
        self.schema_path = schema_path
        with schema_path.open("r", encoding="utf-8") as handle:
            self.schema = json.load(handle)

    def parse(self, text: str) -> LLMDecision:
        json_text = extract_first_json_object(text)
        payload = json.loads(json_text)
        validate(instance=payload, schema=self.schema)
        return LLMDecision.model_validate(payload)
