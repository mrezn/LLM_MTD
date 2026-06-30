from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from eval.types import NormalizedState


@dataclass(slots=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str


class PromptBuilder:
    def __init__(
        self,
        system_path: Path,
        user_template_path: Path,
        hybrid_template_path: Path | None = None,
    ) -> None:
        self.system_path = system_path
        self.user_template_path = user_template_path
        self.hybrid_template_path = hybrid_template_path

    def build_defender_prompt(
        self,
        state: NormalizedState,
        prompt_sections: dict[str, str],
    ) -> PromptBundle:
        system_prompt = self.system_path.read_text(encoding="utf-8")
        user_template = self.user_template_path.read_text(encoding="utf-8")
        return PromptBundle(
            system_prompt=system_prompt,
            user_prompt=user_template.format(
                scenario_id=state.scenario_id,
                target_asset=state.target_asset,
                entry_node=state.entry_node,
                attack_context=prompt_sections["attack_context"],
                qos_context=prompt_sections["qos_context"],
                security_context=prompt_sections["security_context"],
                controller_context=prompt_sections["controller_context"],
                allowed_actions=json.dumps(state.allowed_actions, indent=2),
                active_pool_state=prompt_sections["active_pool_state"],
            ),
        )

    def build_hybrid_prompt(
        self,
        state: NormalizedState,
        prompt_sections: dict[str, str],
        game_defender_shortlist: list[dict[str, Any]],
        game_utility_hints: dict[str, Any],
    ) -> PromptBundle:
        template_path = self.hybrid_template_path or self.user_template_path
        system_prompt = self.system_path.read_text(encoding="utf-8")
        hybrid_template = template_path.read_text(encoding="utf-8")
        return PromptBundle(
            system_prompt=system_prompt,
            user_prompt=hybrid_template.format(
                scenario_id=state.scenario_id,
                target_asset=state.target_asset,
                entry_node=state.entry_node,
                game_defender_shortlist=json.dumps(game_defender_shortlist, indent=2, sort_keys=True),
                game_utility_hints=json.dumps(game_utility_hints, indent=2, sort_keys=True),
                attack_context=prompt_sections["attack_context"],
                qos_context=prompt_sections["qos_context"],
                security_context=prompt_sections["security_context"],
                controller_context=prompt_sections["controller_context"],
                allowed_actions=json.dumps(state.allowed_actions, indent=2),
                active_pool_state=prompt_sections["active_pool_state"],
            ),
        )
