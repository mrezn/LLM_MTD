from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HTTPResult(StrictModel):
    ok: bool
    status_code: int
    url: str
    body_text: str = ""
    parsed_json: dict[str, Any] | list[Any] | None = None
    error: str = ""


class ActivePoolUpdate(StrictModel):
    enabled: bool = False
    promote: list[str] = Field(default_factory=list)
    demote: list[str] = Field(default_factory=list)


class ActivePoolState(StrictModel):
    enabled: bool = False
    active_strategies: list[str] = Field(default_factory=list)
    pool_strategies: list[str] = Field(default_factory=list)


class AttackContext(StrictModel):
    mulval_path: list[str] = Field(default_factory=list)
    risk_score: float = 0.0
    caldera_result: dict[str, Any] | None = None


class QosContext(StrictModel):
    sensor_to_gateway_latency_ms: float = 0.0
    gateway_to_worker_latency_ms: float = 0.0
    edge_to_cloud_latency_ms: float = 0.0
    queue_length: int = 0
    throughput_bps: float = 0.0
    message_loss_rate: float = 0.0


class SecurityContext(StrictModel):
    gateway_seen: bool = False
    worker_seen: bool = False
    cloud_seen: bool = False
    attack_effect_success: bool = False
    defense_success: bool = False


class ControllerContext(StrictModel):
    active_policy_actions: int = 0
    flow_rules_installed: int = 0
    meters_added: int = 0
    ryu_apply_duration_ms: float = 0.0


class NormalizedState(StrictModel):
    scenario_id: str
    timestamp: str
    target_asset: str
    entry_node: str
    attack_context: AttackContext
    qos_context: QosContext
    security_context: SecurityContext
    controller_context: ControllerContext
    allowed_actions: list[str]
    active_pool: ActivePoolState


class LLMDecision(StrictModel):
    selected_defender_strategy: str
    target: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    reasoning_summary: str
    expected_security_gain: float = 0.0
    expected_qos_impact: float = 0.0
    active_pool_update: ActivePoolUpdate = Field(default_factory=ActivePoolUpdate)


class LLMResponseTrace(StrictModel):
    provider: str
    model_name: str
    raw_text: str
    latency_ms: float
    retries_used: int = 0
    prompt_preview: str = ""


class ActionAdaptation(StrictModel):
    recommended_strategy: str
    executed_action: str
    target: str
    payload: dict[str, Any]
    fallback_used: bool = False
    unsupported_strategy: str | None = None
    not_executed_reason: str | None = None
    notes: list[str] = Field(default_factory=list)


class TrialTimestamps(StrictModel):
    started_at: str
    decision_at: str
    executed_at: str
    observed_at: str


class TrialDecisionSummary(StrictModel):
    recommended_strategy: str
    executed_action: str
    fallback_used: bool
    unsupported_strategy: str | None = None


class TrialResult(StrictModel):
    trial_id: str
    scenario_id: str
    model_type: str
    seed: int
    timestamps: TrialTimestamps
    decision: TrialDecisionSummary
    qos_metrics: dict[str, Any]
    security_metrics: dict[str, Any]
    overhead_metrics: dict[str, Any]
    llm_quality_metrics: dict[str, Any]
    raw_state_before: dict[str, Any]
    raw_state_after: dict[str, Any]
    notes: list[str] = Field(default_factory=list)
