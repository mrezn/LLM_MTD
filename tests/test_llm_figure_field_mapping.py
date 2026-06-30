from eval.reports.load_results import _flatten_summary_row


def test_nested_llm_summary_fields_are_mapped():
    row = {
        "scenario_id": "s", "stage_id": 1,
        "llm": {
            "latency_ms": 42, "baseline_alignment": "followed",
            "request_success": True, "parse_success": True,
            "candidate_rankings": [{"id": "D0", "rank": 1}],
        },
    }
    result = _flatten_summary_row(row, method="llm_defender")
    assert result["llm_latency_ms"] == 42
    assert result["llm_baseline_alignment"] == "followed"
    assert result["llm_ranked_candidates"][0]["id"] == "D0"
