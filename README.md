# LLM-MTD Evolutionary Simulator

This project simulates a hospital edge-cloud system under an attacker-defender evolutionary game with an LLM-guided defender. Each episode follows a two-state attack path. The attacker evolves via replicator dynamics, while the defender uses an LLM-augmented replicator-mutator update with pool promotion and demotion controls.

## Requirements
- Python 3.9+ (3.10+ recommended)
- Ollama running locally

## Install
From `llm_mtd_sim/`:

Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ollama setup
```bash
ollama serve
ollama pull llama3.2
ollama pull olmo-3:7b-think
```

## Run simulation
```bash
python main.py --config config.yaml
```

## Run evaluation
```bash
python evaluate.py --methods LLM-Full --num_scenarios 3 --num_trials 5 --horizon 40 --output_dir results_eval --seed 42
```

Note: `evaluate.py` uses the `--horizon` argument for episodes. It does not read `config.yaml` for episode count.

## Output directories
- Simulation outputs (from `main.py`):
  - `results/sim_log.csv`
  - `results/episode_summaries.jsonl`
  - `results/figs/*.png`
- Evaluation outputs (from `evaluate.py`):
  - `results_eval/episodes_*.csv`
  - `results_eval/runs_*.csv`
  - `results_eval/summary.csv`
  - `results_eval/summary_by_scenario.csv`
  - `results_eval/figures/<method>/*.png`
  - `results_eval/figures/success_rate_bars.png`
  - `results_eval/figures/coverage_bars.png`

## Config.yaml quick guide
`config.yaml` is used by `main.py` (the simulator). Key sections:

- `simulation`: run control
  - `seed`, `episodes`, `steps_per_episode`, `z_max`, `gamma`
- `evolutionary`: defender learning
  - `eta`, `omega_A`, `omega_D`, `llm_lambda`, `beta_softmax`
- `attacker`: attacker learning and bounded rationality
  - `eta_A`, `omega_A`, `tau_A`, `eps_A`, `rho_A`, `tau_BR_A`
  - `eta_P`, `omega_P`, `tau_P`, `eps_P`, `rho_P`, `tau_BR_P`
  - `C_switch`, `fitness_transform`, `fitness_clip_min`
- `active_pool`: promotion and demotion rules
  - `max_active`, `promote_every`, `q_new_init`, `demote_q_min`, `demote_patience`
  - `dc_max`, `dc_window`
- `llm`: Ollama settings
  - `ollama_host`, `llm_macro_model`, `llm_summary_model`, `llm_timeout_s`
- `costs`: SAL/SAP cost weights

Example change (episode count for simulation only):
```yaml
simulation:
  episodes: 500
```

## Show figures in GitHub (pics directory)
Create a `pics/` folder inside `llm_mtd_sim/` and copy the figures you want to track in git.

Windows (PowerShell):
```powershell
New-Item -ItemType Directory -Force .\pics | Out-Null
Copy-Item .\results\figs\*.png .\pics\
Copy-Item .\results_eval\figures\LLM-Full\*.png .\pics\
```

macOS/Linux:
```bash
mkdir -p pics
cp results/figs/*.png pics/
cp results_eval/figures/LLM-Full/*.png pics/
```

Then reference them in this README using relative paths:
```markdown
![Defender q](pics/fig_defender_q.png)
![Attacker p](pics/fig_attacker_p.png)
![Robustness](pics/robustness_boxplots.png)
```

## Model overview
- Two attack paths (P1 sensor entry, P2 cloud entry), each with two states per episode.
- SAL/SAP utilities and costs follow the exact formulas in the specification.
- Defender control includes LLM-driven macro distribution, mutation matrix, promotion, and demotion.

## Core formulas (ASCII)
```
beta_y(c*, a_r, pi) = (1 - exp(-c* a_r pi)) / (1 + exp(-c* a_r pi))

theta_xy = 1 - lambda_x * beta_y

SAL = (1 + xi) * C_r_target * sum_x [theta_xy * W(C_x) * R(C_x)]

SAP = (1 - xi) * C_r_target * sum_x [(mu_y*theta_xy + (1 - theta_xy)) * (1 - W(C_x)) * R(C_x)]

AC = sum_r w_r * phi_r

DC = ASSC + NC + AIC

U_A = SAL - AC - G_p
U_D = SAP - DC - R_alpha
```

## LLM roles
- Macro/mutation controller (llama3.2): returns JSON with macro probabilities, mutation matrix, and optional promote/demote keys.
- Episode summary (olmo-3:7b-think): generates a narrative summary per episode.

## Computational complexity
- xi computation uses matrix powers: O(z_max * Z^3), where Z is the number of nodes.
- Payoff matrix construction: O(m * n_act) for m attackers and n_act active defenders.
- LLM overhead is latency-dominated (local Ollama).
