from __future__ import annotations

from pathlib import Path

from eval.reports.report_cli import _infer_emo_root


def test_infer_emo_root_accepts_modular_repo(tmp_path):
    project = tmp_path / "LLM_MTD_modular"
    (project / "environment" / "network").mkdir(parents=True)
    (project / "environment" / "network" / "network_model.py").write_text("# stub\n", encoding="utf-8")
    stage_log = project / "outputs" / "raw" / "live_stage_history.jsonl"
    stage_log.parent.mkdir(parents=True)
    stage_log.write_text("", encoding="utf-8")
    assert _infer_emo_root(None, stage_log) == project
