from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_EXECUTABLE_ACTIONS = [
    "observe",
    "quarantine_sensor",
    "rate_limit",
    "reroute_traffic",
    "release_sensor",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _workspace_root() -> Path:
    return _project_root().parent


def load_env_file(env_path: Path | None) -> None:
    if env_path is None or not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip())


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a mapping: {path}")
    return data


def resolve_path(project_root: Path, value: str | Path | None) -> Path | None:
    if value in (None, ""):
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


@dataclass(slots=True)
class ResolvedConfig:
    project_root: Path
    workspace_root: Path
    config_path: Path
    config: dict[str, Any]
    output_root: Path
    raw_output_dir: Path
    summaries_output_dir: Path
    figures_output_dir: Path
    traces_output_dir: Path

    @classmethod
    def from_model_config(
        cls,
        model_config_path: str | Path,
        env_path: str | Path | None = None,
        output_root: str | Path | None = None,
    ) -> "ResolvedConfig":
        project_root = _project_root()
        workspace_root = _workspace_root()
        env_file = resolve_path(project_root, env_path) if env_path else project_root / ".env"
        load_env_file(env_file)

        config_path = resolve_path(project_root, model_config_path)
        if config_path is None:
            raise ValueError("model_config_path is required")
        config = load_yaml(config_path)

        env_output = os.environ.get("LLM_MTD_EVAL_OUTPUT_DIR", "outputs")
        output_dir = resolve_path(project_root, output_root or env_output)
        if output_dir is None:
            raise ValueError("Could not resolve output directory")
        output_dir.mkdir(parents=True, exist_ok=True)

        raw_output_dir = output_dir / "raw"
        summaries_output_dir = output_dir / "summaries"
        figures_output_dir = output_dir / "figures"
        traces_output_dir = output_dir / "traces"
        for path in (
            raw_output_dir,
            summaries_output_dir,
            figures_output_dir,
            traces_output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        return cls(
            project_root=project_root,
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
            output_root=output_dir,
            raw_output_dir=raw_output_dir,
            summaries_output_dir=summaries_output_dir,
            figures_output_dir=figures_output_dir,
            traces_output_dir=traces_output_dir,
        )

    def model_name(self) -> str:
        return str((self.config.get("model") or {}).get("name", "llm_only"))

    def model_mode(self) -> str:
        return str((self.config.get("model") or {}).get("mode", "llm_only"))

    def llm_config(self) -> dict[str, Any]:
        return dict(self.config.get("llm") or {})

    def trial_config(self) -> dict[str, Any]:
        return dict(self.config.get("trial") or {})

    def emulator_config(self) -> dict[str, Any]:
        emulator = dict(self.config.get("emulator") or {})
        overrides = {
            "core_url": os.environ.get("LLM_MTD_EVAL_CORE_URL"),
            "experiment_summary_url": os.environ.get("LLM_MTD_EVAL_EXPERIMENT_SUMMARY_URL"),
            "ryu_status_url": os.environ.get("LLM_MTD_EVAL_RYU_STATUS_URL"),
            "ryu_metrics_url": os.environ.get("LLM_MTD_EVAL_RYU_METRICS_URL"),
            "ryu_action_url": os.environ.get("LLM_MTD_EVAL_RYU_ACTION_URL"),
            "cloud_policy_url": os.environ.get("LLM_MTD_EVAL_CLOUD_POLICY_URL"),
            "cloud_logger_url": os.environ.get("LLM_MTD_EVAL_CLOUD_LOGGER_URL"),
        }
        for key, value in overrides.items():
            if value:
                emulator[key] = value
        return emulator

    def data_paths(self) -> dict[str, Path | None]:
        data = dict(self.config.get("data") or {})
        scenario_registry = os.environ.get("LLM_MTD_EVAL_SCENARIO_REGISTRY") or data.get(
            "scenario_registry"
        )
        mulval_policy = os.environ.get("LLM_MTD_EVAL_MULVAL_POLICY") or data.get(
            "mulval_policy"
        )
        return {
            "scenario_registry": resolve_path(self.project_root, scenario_registry),
            "mulval_policy": resolve_path(self.project_root, mulval_policy),
        }

    def prompt_paths(self) -> dict[str, Path | None]:
        prompts = dict(self.config.get("prompts") or {})
        return {
            key: resolve_path(self.project_root, value)
            for key, value in prompts.items()
        }

    def schema_paths(self) -> dict[str, Path | None]:
        schemas = dict(self.config.get("schemas") or {})
        return {
            key: resolve_path(self.project_root, value)
            for key, value in schemas.items()
        }

    def active_pool_config(self) -> dict[str, Any]:
        return dict(self.config.get("active_pool") or {})
