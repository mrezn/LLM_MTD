from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import validate

from eval.types import LLMDecision


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences that LLMs commonly wrap JSON in."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def extract_first_json_object(text: str) -> str:
    text = strip_markdown_fences(text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, end_index = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return text[index : index + end_index]
    raise ValueError("Could not find a JSON object in the LLM response")


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
