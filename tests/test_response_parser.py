from __future__ import annotations

import json

from defender.decision.response_parser import ResponseParser, extract_first_json_object


def test_extract_first_json_object_handles_fences_and_extra_text(tmp_path):
    payload = {"selected_defender_strategy": "D1", "target": "sen4", "parameters": {}, "confidence": 0.7, "reasoning_summary": "x", "expected_security_gain": 0.8, "expected_qos_impact": 0.2}
    text = "before\n```json\n" + json.dumps(payload) + "\n```\nafter {\"ignore\":true}"
    assert json.loads(extract_first_json_object(text))["selected_defender_strategy"] == "D1"


def test_response_parser_parses_fenced_json(tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({
        "type": "object",
        "required": ["selected_defender_strategy", "target", "parameters", "confidence", "reasoning_summary", "expected_security_gain", "expected_qos_impact"],
        "properties": {
            "selected_defender_strategy": {"type": "string"},
            "target": {"type": "string"},
            "parameters": {"type": "object"},
            "confidence": {"type": "number"},
            "reasoning_summary": {"type": "string"},
            "expected_security_gain": {"type": "number"},
            "expected_qos_impact": {"type": "number"},
        },
    }), encoding="utf-8")
    parser = ResponseParser(schema_path)
    decision = parser.parse("""```json
{"selected_defender_strategy":"D1","target":"sen4","parameters":{},"confidence":0.9,"reasoning_summary":"test","expected_security_gain":0.8,"expected_qos_impact":0.2}
```""")
    assert decision.selected_defender_strategy == "D1"
