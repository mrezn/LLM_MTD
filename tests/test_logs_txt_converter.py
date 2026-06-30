import json

from tools.jsonl_to_logs_txt import convert


def test_jsonl_converter_writes_readable_stage_line(tmp_path):
    source = tmp_path / "stages.jsonl"
    output = tmp_path / "logs.txt"
    source.write_text(json.dumps({"stage_id": 3, "scenario_id": "s", "attacker_strategy_id": "A1", "defender_strategy_id": "D0"}) + "\n", encoding="utf-8")
    assert convert(source, output) == 1
    assert "stage=3" in output.read_text(encoding="utf-8")
