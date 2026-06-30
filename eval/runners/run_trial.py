from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any

from eval.baselines.game_baseline import select_game_baseline
from eval.baselines.random_baseline import select_random_baseline
from eval.baselines.rule_baseline import select_rule_baseline
from eval.emulator_client.cloud_policy_client import CloudPolicyClient
from eval.emulator_client.core_client import CoreClient
from eval.emulator_client.logger_client import LoggerClient
from eval.emulator_client.ryu_client import RyuClient
from eval.emulator_client.scenario_loader import ScenarioLoader
from defender.actions.action_adapter import ActionAdapter
from defender.actions.constraint_guard import ConstraintGuard
from defender.decision.llm_client import LLMClient
from defender.decision.prompt_builder import PromptBuilder
from defender.decision.response_parser import ResponseParser
from eval.reports.export_csv import append_row
from eval.reports.export_json import write_json
from eval.settings import ResolvedConfig, SUPPORTED_EXECUTABLE_ACTIONS
from eval.state.active_pool_state import build_active_pool_state
from eval.state.feature_builder import build_features
from eval.state.normalizer import build_normalized_state
from eval.state.summarizer import summarize_for_logs, summarize_for_prompt
from eval.types import (
    ActionAdaptation,
    LLMDecision,
    LLMResponseTrace,
    TrialDecisionSummary,
    TrialResult,
    TrialTimestamps,
)
from eval.metrics.metrics_collector import collect_metrics


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_trial_id(model_type: str, scenario_id: str, seed: int) -> str:
    digest = hashlib.sha1(f"{model_type}:{scenario_id}:{seed}:{_utc_now_iso()}".encode("utf-8")).hexdigest()
    return f"trial_{digest[:12]}"


@dataclass(slots=True)
class TrialArtifacts:
    result_path: Path
    trace_path: Path
    csv_path: Path


def _gather_raw_state(
    *,
    config: ResolvedConfig,
    offline_override: bool | None = None,
) -> dict[str, Any]:
    trial_cfg = config.trial_config()
    emulator_cfg = config.emulator_config()
    offline = trial_cfg.get("offline", False) if offline_override is None else offline_override
    if offline:
        return {
            "core_data": {},
            "experiment_summary": {},
            "ryu_status_data": {},
            "ryu_metrics_text": "",
        }

    core_client = CoreClient(
        emulator_cfg["core_url"],
        emulator_cfg.get("experiment_summary_url"),
        timeout_seconds=float(emulator_cfg.get("timeout_seconds", 3.0)),
        retries=int(emulator_cfg.get("retries", 2)),
    )
    ryu_client = RyuClient(
        emulator_cfg["ryu_status_url"],
        emulator_cfg["ryu_metrics_url"],
        emulator_cfg["ryu_action_url"],
        timeout_seconds=float(emulator_cfg.get("timeout_seconds", 3.0)),
        retries=int(emulator_cfg.get("retries", 2)),
    )
    return {
        "core_data": core_client.get_core(),
        "experiment_summary": core_client.get_experiment_summary(),
        "ryu_status_data": ryu_client.get_status(),
        "ryu_metrics_text": ryu_client.get_metrics(),
    }


def _clamp_score(value: float, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _extract_named_float(text: str, name: str) -> float | None:
    patterns = [
        rf"{re.escape(name)}[^0-9\-]*([01](?:\.\d+)?)",
        rf"{re.escape(name.replace('_', ' '))}[^0-9\-]*([01](?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            continue
    return None


def _extract_reasoning_summary(text: str) -> str:
    matches = re.findall(r'"([^"\n]{12,240})"', text)
    if matches:
        return matches[-1].strip()
    compact = " ".join(text.split())
    if not compact:
        return "Recovered a valid defender action from a malformed LLM response."
    sentences = re.split(r"(?<=[.!?])\s+", compact)
    for sentence in sentences:
        cleaned = sentence.strip()
        if len(cleaned) >= 12:
            return cleaned[:240]
    return compact[:240]


def _recover_decision_from_text(text: str, normalized_state) -> LLMDecision | None:
    compact = " ".join(text.split())
    if not compact:
        return None

    lowered = compact.lower()
    allowed_actions = list(normalized_state.allowed_actions or SUPPORTED_EXECUTABLE_ACTIONS)
    action = "observe"
    for candidate in sorted(allowed_actions, key=len, reverse=True):
        variants = {candidate.lower(), candidate.replace("_", " ").lower()}
        if any(re.search(rf"\b{re.escape(variant)}\b", lowered) for variant in variants):
            action = candidate
            break

    if action == "observe":
        target = ""
    else:
        candidates = [
            normalized_state.entry_node,
            normalized_state.target_asset,
            *normalized_state.attack_context.mulval_path,
        ]
        target = next((item for item in candidates if item and item.lower() in lowered), "")
        if not target and action in {"quarantine_sensor", "rate_limit", "release_sensor", "reroute_traffic"}:
            target = normalized_state.entry_node

    parameters: dict[str, Any] = {}
    if action == "rate_limit":
        kbps_match = re.search(r"\bkbps[^0-9]*(\d+)\b", compact, flags=re.IGNORECASE)
        parameters["kbps"] = int(kbps_match.group(1)) if kbps_match else 128
    elif action == "reroute_traffic":
        via_match = re.search(r"\b(s_edge\d+)\b", compact, flags=re.IGNORECASE)
        parameters["via"] = via_match.group(1) if via_match else "s_edge2"

    default_scores = {
        "observe": (0.8, 0.0, 0.0),
        "quarantine_sensor": (0.85, 0.8, 0.65),
        "rate_limit": (0.8, 0.55, 0.25),
        "reroute_traffic": (0.75, 0.4, 0.15),
        "release_sensor": (0.7, 0.1, 0.05),
    }
    default_confidence, default_security_gain, default_qos_impact = default_scores.get(
        action,
        (0.75, 0.1, 0.05),
    )

    confidence = _clamp_score(_extract_named_float(compact, "confidence"), default_confidence)
    security_gain = _clamp_score(
        _extract_named_float(compact, "expected_security_gain"),
        default_security_gain,
    )
    qos_impact = _clamp_score(
        _extract_named_float(compact, "expected_qos_impact"),
        default_qos_impact,
    )

    return LLMDecision(
        selected_defender_strategy=action,
        target=target,
        parameters=parameters,
        confidence=confidence,
        reasoning_summary=_extract_reasoning_summary(compact),
        expected_security_gain=security_gain,
        expected_qos_impact=qos_impact,
        active_pool_update={"enabled": False, "promote": [], "demote": []},
    )


def _select_decision(
    *,
    config: ResolvedConfig,
    normalized_state,
    scenario_id: str,
    seed: int,
    offline: bool = False,
    dry_run: bool = True,
) -> tuple[LLMDecision, LLMResponseTrace, list[str]]:
    mode = config.model_mode()
    if mode == "game_only":
        decision = select_game_baseline(normalized_state)
        trace = LLMResponseTrace(
            provider="baseline",
            model_name="game_only",
            raw_text=decision.model_dump_json(),
            latency_ms=0.0,
            retries_used=0,
            prompt_preview="",
        )
        return decision, trace, []
    if mode == "rule_only":
        decision = select_rule_baseline(normalized_state)
        trace = LLMResponseTrace(
            provider="baseline",
            model_name="rule_only",
            raw_text=decision.model_dump_json(),
            latency_ms=0.0,
            retries_used=0,
            prompt_preview="",
        )
        return decision, trace, []
    if mode == "random_baseline":
        decision = select_random_baseline(normalized_state, seed=seed)
        trace = LLMResponseTrace(
            provider="baseline",
            model_name="random_baseline",
            raw_text=decision.model_dump_json(),
            latency_ms=0.0,
            retries_used=0,
            prompt_preview="",
        )
        return decision, trace, []

    prompt_paths = config.prompt_paths()
    builder = PromptBuilder(
        prompt_paths["system"],
        prompt_paths["user_template"],
        prompt_paths.get("hybrid_user_template"),
    )
    features = build_features(normalized_state)
    prompt_sections = summarize_for_prompt(normalized_state, features)
    if mode == "hybrid_game_llm":
        shortlist = [
            select_game_baseline(normalized_state).model_dump(),
        ]
        utility_hints = {"game_prior": "Phase 1 placeholder shortlist"}
        prompt_bundle = builder.build_hybrid_prompt(
            normalized_state,
            prompt_sections,
            shortlist,
            utility_hints,
        )
    else:
        prompt_bundle = builder.build_defender_prompt(normalized_state, prompt_sections)

    llm_config = config.llm_config()
    notes: list[str] = []
    if offline and dry_run and str(llm_config.get("provider", "mock")) != "mock":
        llm_config = {
            **llm_config,
            "provider": "mock",
            "model_name": f"mock::{llm_config.get('model_name', 'offline-fallback')}",
        }
        notes.append("offline_dry_run_used_mock_llm_provider")

    llm_client = LLMClient(llm_config)
    trace = llm_client.complete_json(
        prompt_bundle.system_prompt,
        prompt_bundle.user_prompt,
        state=normalized_state,
    )
    parser = ResponseParser(config.schema_paths()["llm_decision"])
    try:
        decision = parser.parse(trace.raw_text)
        return decision, trace, notes
    except Exception:
        recovered = _recover_decision_from_text(trace.raw_text, normalized_state)
        if recovered is None:
            raise
        notes.append("heuristic_llm_decision_recovery_used")
        return recovered, trace, notes


def _save_artifacts(
    *,
    config: ResolvedConfig,
    trial_id: str,
    result: TrialResult,
    trace: LLMResponseTrace,
) -> TrialArtifacts:
    result_path = config.raw_output_dir / f"{trial_id}.json"
    trace_path = config.traces_output_dir / f"{trial_id}_trace.json"
    csv_path = config.summaries_output_dir / "trial_summaries.csv"
    write_json(result_path, result.model_dump(mode="json"))
    write_json(trace_path, trace.model_dump(mode="json"))
    append_row(
        csv_path,
        {
            "trial_id": result.trial_id,
            "scenario_id": result.scenario_id,
            "model_type": result.model_type,
            "seed": result.seed,
            "recommended_strategy": result.decision.recommended_strategy,
            "executed_action": result.decision.executed_action,
            "fallback_used": result.decision.fallback_used,
            "decision_latency_ms": result.llm_quality_metrics.get("decision_latency_ms", 0.0),
        },
    )
    return TrialArtifacts(result_path=result_path, trace_path=trace_path, csv_path=csv_path)


def run_trial(
    *,
    model_config_path: str | Path,
    scenario_id: str,
    seed: int = 42,
    offline_override: bool | None = None,
    dry_run_override: bool | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    config = ResolvedConfig.from_model_config(model_config_path, output_root=output_root)
    started_at = _utc_now_iso()
    trial_cfg = config.trial_config()
    offline = bool(trial_cfg.get("offline", False)) if offline_override is None else bool(offline_override)
    dry_run = bool(trial_cfg.get("dry_run", True)) if dry_run_override is None else bool(dry_run_override)
    loader = ScenarioLoader(
        config.data_paths()["scenario_registry"],
        config.data_paths()["mulval_policy"],
    )
    bundle = loader.scenario_bundle(scenario_id)
    scenario = bundle["scenario"]
    if not scenario:
        raise ValueError(f"Unknown scenario_id: {scenario_id}")

    raw_before = _gather_raw_state(config=config, offline_override=offline)
    active_pool_state = build_active_pool_state(config.active_pool_config())
    normalized_before = build_normalized_state(
        core_data=raw_before["core_data"],
        experiment_summary=raw_before["experiment_summary"],
        ryu_status_data=raw_before["ryu_status_data"],
        ryu_metrics_text=raw_before["ryu_metrics_text"],
        scenario=scenario,
        mulval_policy=bundle["mulval_policy"],
        active_pool_state=active_pool_state,
    )

    decision, trace, decision_notes = _select_decision(
        config=config,
        normalized_state=normalized_before,
        scenario_id=scenario_id,
        seed=seed,
        offline=offline,
        dry_run=dry_run,
    )
    decision_at = _utc_now_iso()
    adapter = ActionAdapter(ConstraintGuard())
    adaptation = adapter.adapt(decision, normalized_before)

    executed_at = _utc_now_iso()
    raw_after = raw_before
    normalized_after = normalized_before

    if not dry_run and not offline:
        emulator_cfg = config.emulator_config()
        cloud_policy_url = str(emulator_cfg.get("cloud_policy_url", "") or "")
        cloud_logger_url = str(emulator_cfg.get("cloud_logger_url", "") or "")
        ryu_client = RyuClient(
            emulator_cfg["ryu_status_url"],
            emulator_cfg["ryu_metrics_url"],
            emulator_cfg["ryu_action_url"],
            timeout_seconds=float(emulator_cfg.get("timeout_seconds", 3.0)),
            retries=int(emulator_cfg.get("retries", 2)),
        )
        if cloud_policy_url:
            cloud_policy = CloudPolicyClient(
                cloud_policy_url,
                timeout_seconds=float(emulator_cfg.get("timeout_seconds", 3.0)),
                retries=int(emulator_cfg.get("retries", 2)),
            )
            cloud_policy.post_context(
                {
                    "scenario_id": scenario_id,
                    "normalized_state": normalized_before.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                }
            )
        ryu_client.apply_action(adaptation.payload)
        if cloud_logger_url:
            logger_client = LoggerClient(
                cloud_logger_url,
                timeout_seconds=float(emulator_cfg.get("timeout_seconds", 3.0)),
                retries=int(emulator_cfg.get("retries", 2)),
            )
            logger_client.post_event(
                {
                    "event_type": "llm_mtd_eval_decision",
                    "scenario_id": scenario_id,
                    "decision": decision.model_dump(mode="json"),
                    "adaptation": adaptation.model_dump(mode="json"),
                    "summary": summarize_for_logs(normalized_before, build_features(normalized_before)),
                }
            )
        raw_after = _gather_raw_state(config=config, offline_override=False)
        normalized_after = build_normalized_state(
            core_data=raw_after["core_data"],
            experiment_summary=raw_after["experiment_summary"],
            ryu_status_data=raw_after["ryu_status_data"],
            ryu_metrics_text=raw_after["ryu_metrics_text"],
            scenario=scenario,
            mulval_policy=bundle["mulval_policy"],
            active_pool_state=active_pool_state,
        )

    observed_at = _utc_now_iso()
    metrics = collect_metrics(
        before=normalized_before,
        after=normalized_after,
        decision=decision,
        trace=trace,
        adaptation=adaptation,
    )

    trial_id = _make_trial_id(config.model_mode(), scenario_id, seed)
    result = TrialResult(
        trial_id=trial_id,
        scenario_id=scenario_id,
        model_type=config.model_mode(),
        seed=seed,
        timestamps=TrialTimestamps(
            started_at=started_at,
            decision_at=decision_at,
            executed_at=executed_at,
            observed_at=observed_at,
        ),
        decision=TrialDecisionSummary(
            recommended_strategy=decision.selected_defender_strategy,
            executed_action=adaptation.executed_action,
            fallback_used=adaptation.fallback_used,
            unsupported_strategy=adaptation.unsupported_strategy,
        ),
        qos_metrics=metrics["qos_metrics"],
        security_metrics=metrics["security_metrics"],
        overhead_metrics=metrics["overhead_metrics"],
        llm_quality_metrics=metrics["llm_quality_metrics"],
        raw_state_before={
            "normalized_state": normalized_before.model_dump(mode="json"),
            **raw_before,
        },
        raw_state_after={
            "normalized_state": normalized_after.model_dump(mode="json"),
            **raw_after,
        },
        notes=list(adaptation.notes),
    )
    if decision_notes:
        result.notes.extend(decision_notes)
    artifacts = _save_artifacts(config=config, trial_id=trial_id, result=result, trace=trace)
    return {
        "result": result.model_dump(mode="json"),
        "artifacts": {
            "result_path": str(artifacts.result_path),
            "trace_path": str(artifacts.trace_path),
            "csv_path": str(artifacts.csv_path),
        },
    }
