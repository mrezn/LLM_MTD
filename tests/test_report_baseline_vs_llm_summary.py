import pandas as pd

from eval.reports.build_tables import build_baseline_vs_llm_summary_table


def test_summary_keeps_baseline_and_llm_sources_separate():
    frame = pd.DataFrame([
        {"method": "baseline_game", "scenario_id": "s", "comparable_stage": True, "attack_effect_success": False},
        {"method": "llm_defender", "scenario_id": "s", "comparable_stage": True, "attack_effect_success": True, "llm_latency_ms": 12},
    ])
    result = build_baseline_vs_llm_summary_table(frame)
    assert set(result["method"]) == {"Baseline game", "LLM defender"}
    assert result.loc[result["method"] == "LLM defender", "attack_success_rate"].iloc[0] == 1.0
