from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ScenarioLoader:
    def __init__(self, scenario_registry_path: Path | None, mulval_policy_path: Path | None) -> None:
        self.scenario_registry_path = scenario_registry_path
        self.mulval_policy_path = mulval_policy_path

    def load_scenarios(self) -> list[dict[str, Any]]:
        if self.scenario_registry_path is None or not self.scenario_registry_path.exists():
            return []
        with self.scenario_registry_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []

    def get_scenario(self, scenario_id: str) -> dict[str, Any]:
        for scenario in self.load_scenarios():
            if str(scenario.get("scenario_id", "")) == scenario_id:
                return scenario
        return {}

    def load_mulval_policy(self) -> dict[str, Any]:
        if self.mulval_policy_path is None or not self.mulval_policy_path.exists():
            return {}
        with self.mulval_policy_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}

    def risk_score_for_path(self, path: list[str]) -> float:
        policy = self.load_mulval_policy()
        risk_scores = policy.get("path_risk_scores") or {}
        key = "->".join(path)
        value = risk_scores.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.5

    def scenario_bundle(self, scenario_id: str) -> dict[str, Any]:
        scenario = self.get_scenario(scenario_id)
        mulval_policy = self.load_mulval_policy()
        path = list(scenario.get("mulval_path") or [])
        return {
            "scenario": scenario,
            "mulval_policy": mulval_policy,
            "mulval_path": path,
            "risk_score": self.risk_score_for_path(path) if path else 0.5,
        }
