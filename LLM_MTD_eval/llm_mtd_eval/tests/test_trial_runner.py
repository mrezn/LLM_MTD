from __future__ import annotations

from pathlib import Path

import yaml

from llm_mtd_eval.evaluators.run_trial import run_trial


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_offline_trial_runner_creates_outputs(tmp_path: Path) -> None:
    source_model_config = PROJECT_ROOT / "configs" / "models" / "llm_only.yaml"
    model_config = yaml.safe_load(source_model_config.read_text(encoding="utf-8"))
    model_config["llm"]["provider"] = "mock"
    model_config["llm"]["model_name"] = "mock-defender-v1"

    model_config_path = tmp_path / "llm_only_mock.yaml"
    model_config_path.write_text(yaml.safe_dump(model_config, sort_keys=False), encoding="utf-8")

    payload = run_trial(
        model_config_path=model_config_path,
        scenario_id="sen4_edge2_clouddb",
        seed=42,
        offline_override=True,
        dry_run_override=True,
        output_root=tmp_path,
    )
    result = payload["result"]
    artifacts = payload["artifacts"]
    assert result["scenario_id"] == "sen4_edge2_clouddb"
    assert result["decision"]["executed_action"] in {"observe", "quarantine_sensor", "rate_limit", "reroute_traffic", "release_sensor"}
    assert Path(artifacts["result_path"]).exists()
    assert Path(artifacts["trace_path"]).exists()
    assert Path(artifacts["csv_path"]).exists()
