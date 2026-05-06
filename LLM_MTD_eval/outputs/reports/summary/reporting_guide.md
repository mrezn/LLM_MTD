# Reporting Guide

This directory contains publication-oriented tables and figures generated from the live `LLM_MTD_eval` outputs and the baseline `LLM_MTD_emo` strategy logs.

## Tables

- `environment_setup.csv`: summarizes the emulated deployment layers, representative nodes, and resource profiles so the paper can describe realism and compute constraints clearly.
- `scenario_attack_setup.csv`: lists each scenario, the attacker entry point, the MulVAL path under study, the live attack family, and the defender action candidates evaluated for that path.
- `formal_to_observable_mapping.csv`: maps the formal game-model terms used in the paper to the live metrics actually recorded in the evaluator and emulator logs.
- `baseline_vs_llm_summary.csv`: provides the main baseline-versus-LLM comparison across attack success, defense success, defense confirmation, QoS tradeoffs, controller overhead, and LLM reliability.
- `stage_validity_summary.csv`: counts total, paper-valid, fallback-only, timeout-failed, and defense-applied-but-ineffective stages so invalid live attempts are visible without entering the main paper comparison.
- `stage_case_study.csv`: captures stage-by-stage records for a selected live evaluation trace, including selected strategies, observed state, and the narrative summary text.
- `llm_vs_baseline_decision_alignment.csv`: records whether the LLM followed or overrode the baseline top-utility defender on each live stage and what outcome followed.
- `llm_candidate_ranking_case_study.csv`: expands the LLM candidate ranking into one row per active defender candidate, preserving candidate-level tradeoff estimates.

## Figures

- `attack_defense_outcomes_by_method.png`: grouped comparison of attack success, defense success, and defense confirmation rates.
- `qos_tradeoff_by_method_sensor_to_edge_latency_delta_ms.png`: sensor-to-edge latency tradeoff by method.
- `qos_tradeoff_by_method_edge_to_cloud_latency_delta_ms.png`: edge-to-cloud latency tradeoff by method.
- `qos_tradeoff_by_method_throughput_delta_bps.png`: throughput tradeoff by method.
- `controller_overhead_by_method.png`: controller overhead comparison using rules installed, meters added, and apply latency.
- `defender_action_distribution.png`: stacked distribution of defender actions selected by each method.
- `population_evolution.png`: attacker and defender population-share trajectories across stages for the selected scenario.
- `llm_decision_quality.png`: LLM parse, fallback, recovery, and stage-success rates for quality auditing.
- `llm_baseline_alignment.png`: compares how often the LLM followed versus overrode the baseline defender ranking and what defense success rate each case achieved.
- `llm_candidate_tradeoff_scatter.png`: plots candidate-specific expected security gain against expected QoS impact, highlighting the chosen defense per stage.
- `llm_latency_distribution.png`: distribution of LLM inference latency across recorded stages.
- `llm_decision_timing_vs_path_stage.png`: relates defender choice timing to attack-path stage so the paper can discuss late versus early interventions.
- `stage_trace_case_study.png`: stage-level attack or defense progression trace with defender strategy annotations.

Each figure also writes a sibling CSV with the same basename inside `outputs/reports/figures/`. For example, `llm_baseline_alignment.png` is accompanied by `llm_baseline_alignment.csv`, which contains the exact plotting data used to generate the chart.

## How To Use These In The Paper

### Experimental Setup

Use `environment_setup.csv` together with `scenario_attack_setup.csv` to describe the layered edge-cloud deployment and the concrete attack paths tested. These tables are the clearest place to separate analytical validation assumptions from live emulation behavior.

Suggested text:

> We evaluated the policy layer on a resource-constrained edge-cloud emulation that models sensor, edge gateway, edge worker, and cloud service roles under explicit CPU and memory limits. Each scenario specifies an attacker entry node, a MulVAL-derived path toward the protected cloud asset, and a constrained defender action set that can be executed through the Ryu-based control plane.

### Attack Generation

Use `scenario_attack_setup.csv` and `stage_case_study.csv` to explain how attacks are derived from formal paths but realized with concrete Caldera-backed live attack types.

Suggested text:

> Attack generation remained grounded in the same scenario registry and MulVAL path structure used by the baseline strategy layer. The live evaluator dispatches the selected attacker plan into the emulation and then measures observable progression signals such as gateway visibility, worker activity, and cloud reachability rather than relying on abstract win labels alone.

### LLM Role And Inference-Time Usage

Use `baseline_vs_llm_summary.csv`, `llm_decision_quality`, and `llm_latency_distribution` to explain what the LLM actually contributes and what its inference cost looks like.

Suggested text:

> The LLM was used as a defender-policy reasoner over the currently active candidate set. It received the live attack-path stage, recent stage-memory signals, candidate-specific tradeoff features, and the baseline game-model priors, then produced a ranked candidate list and a final defender selection. We therefore report not only defense outcomes but also when the LLM followed or overrode the baseline ranking, what tradeoff it cited, and what latency or fallback cost was incurred online.

### Limitations

Use `formal_to_observable_mapping.csv`, the QoS figures, and the controller-overhead figure to discuss where live observables remain proxies for the formal game quantities.

Suggested text:

> Several formal terms in the game model, such as attack value and defense cost, are operationalized through observable proxies rather than directly measured latent variables. We therefore report the mapping between formal terms and recorded metrics, and we separate controller confirmation, observable containment, and end-to-end defense success to avoid overstating mitigation effectiveness.
