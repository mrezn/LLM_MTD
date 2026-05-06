# LLM_MTD_evaldwdw

`LLM_MTD_eval` is an evaluation layer that combines:

- the analytical LLM-MTD model from `LLM_MTD`
- the live edge-cloud emulation substrate from `LLM_MTD_emo`

Version 1 keeps the existing emulator untouched and integrates through its HTTP
APIs and scenario files.

## Current Scope

This first implementation delivers Phase 1:

- repository scaffold
- emulator HTTP clients
- normalized state builder
- prompt builder
- strict JSON LLM response parsing
- action adapter with safe fallback behavior
- single-trial dry-run evaluator
- pytest coverage for parser, adapter, normalizer, and trial runner

Later phases already have placeholder modules and config files so we can extend
the project without restructuring the repo.

## Architecture

`LLM_MTD_eval` sits above the emulator:

1. fetches `/core`, `/experiment/summary`, `/mtd/status`, and `/mtd/metrics`
2. loads scenario and MulVAL context from `LLM_MTD_emo`
3. builds a normalized evaluation state
4. prompts an LLM or mock LLM policy agent
5. validates and adapts the decision into the emulator action format
6. saves structured trial outputs

In Phase 1 the evaluator runs in dry-run mode by default and does not apply any
Ryu action.

The repository now also includes a live `run-stage` path that mirrors the
baseline emulator stage loop, keeps the baseline attacker/game update, and lets
the configured LLM choose the defender from the active defender strategy set.

## Quick Start

From the workspace root:

```powershell
cd LLM_MTD_eval
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest llm_mtd_eval/tests
llm-mtd-eval run-trial --model-config configs/models/llm_only.yaml --scenario-id sen4_edge2_clouddb --offline --dry-run
```

The resulting trial JSON is written to `outputs/raw/`.
When `--offline --dry-run` is used with a live LLM config, the evaluator falls
back to the built-in mock provider so the quick start remains self-contained.

To run one live attacker-defender stage with LLM defender selection:

```bash
llm-mtd-eval run-stage \
  --model-config configs/models/llm_only.yaml \
  --scenario-id sen4_edge2_clouddb \
  --core-url "http://127.0.0.1:${CORE_PORT}/core" \
  --mtd-status-url "http://127.0.0.1:8080/mtd/status" \
  --mtd-metrics-url "http://127.0.0.1:8080/mtd/metrics" \
  --cloud-policy-url "http://127.0.0.1:${POLICY_PORT}/context" \
  --cloud-logger-url "http://127.0.0.1:${LOGGER_PORT}/attack/event" \
  --execute-attacker \
  --attacker-dispatch-url "http://127.0.0.1:9000/caldera/dispatch" \
  --execute-defender \
  --observe-delay-seconds 45
```

The live stage runner writes:

- `outputs/raw/live_stage_history.jsonl`
- `outputs/raw/live_decision_trace.jsonl`
- `outputs/raw/stage_summaries.jsonl`
- `outputs/raw/live_population_state.json`

To point the dashboard at the evaluator logs, start the dashboard server with:

```bash
DASHBOARD_DECISION_TRACE_FILE=/home/reza/LLM_MTD_eval/outputs/raw/live_decision_trace.jsonl \
DASHBOARD_STAGE_HISTORY_FILE=/home/reza/LLM_MTD_eval/outputs/raw/live_stage_history.jsonl \
python3 /home/reza/LLM_MTD_emo/dashboard_server.py
```

To build paper-ready tables and figures from evaluator and baseline outputs:

```bash
llm-mtd-eval build-report \
  --eval-stage-history outputs/raw/live_stage_history.jsonl \
  --eval-decision-trace outputs/raw/live_decision_trace.jsonl \
  --eval-stage-summaries outputs/raw/stage_summaries.jsonl \
  --eval-population outputs/raw/live_population_state.json \
  --baseline-stage-history /home/reza/LLM_MTD_emo/integrations/strategy/stage_history.jsonl \
  --baseline-decision-trace /home/reza/LLM_MTD_emo/integrations/strategy/decision_trace.jsonl \
  --baseline-population /home/reza/LLM_MTD_emo/integrations/strategy/population_state.json \
  --output-dir outputs/reports \
  --paper-mode
```

This writes CSV tables under `outputs/reports/tables/`, figures under
`outputs/reports/figures/`, and markdown or JSON reporting notes under
`outputs/reports/summary/`.

## Project Layout

```text
LLM_MTD_eval/
|- README.md
|- pyproject.toml
|- requirements.txt
|- .env.example
|- configs/
|- llm_mtd_eval/
|- outputs/
`- docs/
```

## Phase Roadmap

- Phase 1: dry-run evaluator and schema-validated LLM path
- Phase 2: live defender execution through cloud_policy and Ryu
- Phase 3: baselines and batch experiments
- Phase 4: hybrid game + LLM mode
- Phase 5: optional logical active/pool management
- Phase 6: reports and plots
