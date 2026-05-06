from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evaluators.run_stage import run_stage
from .evaluators.run_trial import run_trial
from .logging_utils import configure_logging
from .reports.report_cli import add_build_report_parser, build_report_from_args
from .settings import ResolvedConfig
from .state.active_pool_state import build_active_pool_state
from .state.normalizer import build_normalized_state
from .emulator_client.scenario_loader import ScenarioLoader


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM_MTD_eval CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_trial_parser = subparsers.add_parser("run-trial", help="Run one dry-run or live evaluation trial.")
    run_trial_parser.add_argument("--model-config", required=True)
    run_trial_parser.add_argument("--scenario-id", required=True)
    run_trial_parser.add_argument("--seed", type=int, default=42)
    run_trial_parser.add_argument("--offline", action=argparse.BooleanOptionalAction, default=None)
    run_trial_parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    run_trial_parser.add_argument("--output-root", default=None)

    run_stage_parser = subparsers.add_parser(
        "run-stage",
        help="Run one live attacker-defender stage with baseline attacker selection and LLM defender selection.",
    )
    run_stage_parser.add_argument("--model-config", required=True)
    run_stage_parser.add_argument("--scenario-id", required=True)
    run_stage_parser.add_argument("--strategy-space", default=None)
    run_stage_parser.add_argument("--scenario-registry", default=None)
    run_stage_parser.add_argument("--mulval-policy", default=None)
    run_stage_parser.add_argument("--core-url", default=None)
    run_stage_parser.add_argument("--mtd-metrics-url", default=None)
    run_stage_parser.add_argument("--mtd-status-url", default=None)
    run_stage_parser.add_argument("--cloud-policy-url", default=None)
    run_stage_parser.add_argument("--cloud-logger-url", default=None)
    run_stage_parser.add_argument("--ryu-action-url", default=None)
    run_stage_parser.add_argument("--attacker-dispatch-url", default=None)
    run_stage_parser.add_argument("--execute-attacker", action="store_true")
    run_stage_parser.add_argument("--execute-defender", action=argparse.BooleanOptionalAction, default=None)
    run_stage_parser.add_argument("--observe-delay-seconds", type=float, default=None)
    run_stage_parser.add_argument("--selection-mode", choices=["dominant", "sample"], default=None)
    run_stage_parser.add_argument("--random-seed", type=int, default=None)
    run_stage_parser.add_argument("--population-file", default=None)
    run_stage_parser.add_argument("--stage-log", default=None)
    run_stage_parser.add_argument("--decision-trace-log", default=None)
    run_stage_parser.add_argument("--summary-log", default=None)
    run_stage_parser.add_argument("--output-root", default=None)
    run_stage_parser.add_argument("--timeout-seconds", type=float, default=None)
    run_stage_parser.add_argument("--llm-timeout-seconds", type=float, default=None)
    run_stage_parser.add_argument("--llm-max-retries", type=int, default=None)
    run_stage_parser.add_argument("--llm-compact-prompt", action="store_true")
    run_stage_parser.add_argument("--llm-max-candidate-fields", type=int, default=None)
    run_stage_parser.add_argument("--strict-preconditions", action="store_true")
    run_stage_parser.add_argument("--max-attack-cost", type=float, default=1.0)
    run_stage_parser.add_argument("--max-defense-cost", type=float, default=1.0)
    run_stage_parser.add_argument("--no-disruptive-defense", action="store_true")
    run_stage_parser.add_argument("--offline", action="store_true")
    run_stage_parser.add_argument("--no-observe-next-state", action="store_true")
    run_stage_parser.add_argument("--no-population-load", action="store_true")
    run_stage_parser.add_argument("--no-save-population", action="store_true")
    run_stage_parser.add_argument("--no-stage-log", action="store_true")
    run_stage_parser.add_argument("--no-decision-trace-log", action="store_true")
    run_stage_parser.add_argument("--no-auto-defense-event", action="store_true")

    build_state_parser = subparsers.add_parser("build-state", help="Build and print one normalized state.")
    build_state_parser.add_argument("--model-config", required=True)
    build_state_parser.add_argument("--scenario-id", required=True)

    add_build_report_parser(subparsers)

    return parser


def _command_build_state(args: argparse.Namespace) -> dict[str, Any]:
    config = ResolvedConfig.from_model_config(args.model_config)
    loader = ScenarioLoader(
        config.data_paths()["scenario_registry"],
        config.data_paths()["mulval_policy"],
    )
    bundle = loader.scenario_bundle(args.scenario_id)
    state = build_normalized_state(
        core_data={},
        ryu_status_data={},
        ryu_metrics_text="",
        scenario=bundle["scenario"],
        mulval_policy=bundle["mulval_policy"],
        active_pool_state=build_active_pool_state(config.active_pool_config()),
    )
    return state.model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_arg_parser().parse_args(argv)

    if args.command == "run-trial":
        payload = run_trial(
            model_config_path=Path(args.model_config),
            scenario_id=args.scenario_id,
            seed=args.seed,
            offline_override=args.offline,
            dry_run_override=args.dry_run,
            output_root=args.output_root,
        )
    elif args.command == "run-stage":
        payload = run_stage(
            model_config_path=Path(args.model_config),
            scenario_id=args.scenario_id,
            strategy_space=args.strategy_space,
            scenario_registry=args.scenario_registry,
            mulval_policy=args.mulval_policy,
            core_url=args.core_url,
            mtd_metrics_url=args.mtd_metrics_url,
            mtd_status_url=args.mtd_status_url,
            cloud_policy_url=args.cloud_policy_url,
            cloud_logger_url=args.cloud_logger_url,
            ryu_action_url=args.ryu_action_url,
            attacker_dispatch_url=args.attacker_dispatch_url,
            execute_attacker=args.execute_attacker,
            execute_defender=args.execute_defender,
            observe_delay_seconds=args.observe_delay_seconds,
            selection_mode=args.selection_mode,
            random_seed=args.random_seed,
            population_file=args.population_file,
            stage_log=args.stage_log,
            decision_trace_log=args.decision_trace_log,
            summary_log=args.summary_log,
            output_root=args.output_root,
            timeout_seconds=args.timeout_seconds,
            llm_timeout_seconds=args.llm_timeout_seconds,
            llm_max_retries=args.llm_max_retries,
            llm_compact_prompt=args.llm_compact_prompt,
            llm_max_candidate_fields=args.llm_max_candidate_fields,
            strict_preconditions=args.strict_preconditions,
            max_attack_cost=args.max_attack_cost,
            max_defense_cost=args.max_defense_cost,
            no_disruptive_defense=args.no_disruptive_defense,
            offline=args.offline,
            no_observe_next_state=args.no_observe_next_state,
            no_population_load=args.no_population_load,
            no_save_population=args.no_save_population,
            no_stage_log=args.no_stage_log,
            no_decision_trace_log=args.no_decision_trace_log,
            no_auto_defense_event=args.no_auto_defense_event,
        )
    elif args.command == "build-state":
        payload = _command_build_state(args)
    elif args.command == "build-report":
        payload = build_report_from_args(args)
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
