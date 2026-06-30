import json

from eval.runners.run_stage import consecutive_stage_kind_count


def test_consecutive_warmup_count_stops_at_non_warmup(tmp_path):
    path = tmp_path / "summary.jsonl"
    rows = ["experimental", "warmup", "warmup", "warmup"]
    path.write_text("\n".join(json.dumps({"scenario_id": "s", "stage_validation": {"stage_kind": kind}}) for kind in rows), encoding="utf-8")
    assert consecutive_stage_kind_count(path, "s", "warmup") == 3
