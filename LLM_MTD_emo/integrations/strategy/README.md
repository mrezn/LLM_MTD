# Strategy Layer

This folder adds a game-ready strategy-selection layer above the existing
MulVAL, Caldera, cloud_policy, and Ryu pieces.

The strategy layer runs one repeated-game stage at a time:

1. Build the current system state `S_t`.
2. Filter the full attacker/defender libraries into active lists.
3. Compute attacker and defender utilities using numeric proxies for the formal
   terms.
4. Apply replicator-style population updates.
5. Select one attacker strategy and one defender strategy.
6. Optionally dispatch the attacker plan and defender action.

By default, execution is dry-run. Nothing is sent to Caldera or Ryu unless the
runtime is called with explicit execution flags.

## Files

- `strategy_space.json`: master attacker/defender strategy registry.
- `state_builder.py`: builds `S_t` from `/core`, `/mtd/metrics`, `/mtd/status`,
  local MulVAL policy JSON, and `integrations/attack_scenarios.json`.
- `strategy_manager.py`: loads the full strategy library and builds
  `A_active(t)` and `D_active(t)`.
- `game_model.py`: computes utility proxies and evolutionary updates.
- `policy_selector.py`: selects strategies from the evolved population shares.
- `strategy_runtime.py`: orchestrates a full stage and optionally dispatches
  actions.

## Game Model

The attacker and defender both maintain mixed population shares over their
current active strategy lists:

```text
sum_i p_i(t) = 1
sum_j q_j(t) = 1
```

The implemented update is a bounded replicator-style update:

```text
p_i(t+1) = p_i(t) + eta_A * p_i(t) * (U_A(i) - avg_U_A)
q_j(t+1) = q_j(t) + eta_D * q_j(t) * (U_D(j) - avg_U_D)
```

The utilities follow the simplified formal model:

```text
U_A = SAL - omega_AC * AC - omega_Gp * G_p
U_D = SAP + omega_R * R_alpha - omega_DC * DC
```

The first implementation uses numeric proxies:

- `SAL`: target criticality, path depth, attack effect, and defense reduction.
- `AC`: attacker strategy metadata: time, resources, knowledge, risk,
  detectability.
- `G_p`: regulatory/detection penalty using `beta * theta`.
- `SAP`: mission value protected, attack pressure, and action effectiveness.
- `DC`: defender metadata plus controller/QoS/resource overhead.
- `R_alpha`: small incentive for useful defense context/logging.

## Run Offline

From the project root:

```bash
python integrations/strategy/strategy_runtime.py \
  --offline \
  --scenario-id sen4_edge2_clouddb \
  --no-save-population \
  --no-stage-log
```

This builds a state from local scenario files, computes active strategies,
updates population shares, and prints the selected attacker/defender pair.

## Run Against The Live Lab

Start the usual stack first:

```bash
ryu-manager --observe-links controller_app.py
sudo python3 topology.py
python3 dashboard_server.py
```

Then run one strategy stage:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb
```

The default `/core` URL is the dashboard proxy:

```text
http://127.0.0.1:8088/core
```

The default controller metrics URL is:

```text
http://127.0.0.1:8080/mtd/metrics
```

If the host cannot reach the dashboard proxy or service-fabric IPs, the runtime
now falls back through the running Docker containers:

```text
mn.cloud_metrics -> http://127.0.0.1:8000/core
mn.cloud_policy  -> http://127.0.0.1:8000/context and /decide
```

`dashboard_server.py` now also auto-discovers the current host-mapped
`cloud_metrics` port on `127.0.0.1` before it falls back to Docker execution
inside `mn.cloud_metrics`.

The fallback is enabled by default. Disable or retarget it with:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --cloud-metrics-container mn.cloud_metrics \
  --cloud-policy-docker-container mn.cloud_policy
```

Use `--no-core-docker-fallback` or `--no-cloud-policy-docker-fallback` when the
host URLs are guaranteed to be reachable and you want direct HTTP failures to
surface immediately.

## MulVAL To Caldera

MulVAL stays in the candidate-generation role. The runtime reads the parsed
MulVAL policy JSON and uses its paths, entry nodes, targets, and risk scores to
activate attacker strategies whose path is currently plausible.

The selected attacker strategy then becomes a Caldera execution plan. Current
adversary mappings are:

- `sensor_to_edge` -> `integrations/caldera/adversaries/sensor_to_edge.yml`
- `edge_to_cloud` -> `integrations/caldera/adversaries/edge_to_cloud.yml`
- `dual_homed_sensor_path` -> `integrations/caldera/adversaries/dual_homed_sensor_path.yml`

The first lab workflow can still launch Caldera manually. To send the execution
plan to your own Caldera bridge:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --execute-attacker \
  --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch
```

When you also pass `--cloud-logger-url` and `--cloud-policy-url`, the runtime
now includes those current callback URLs in the dispatch payload. That lets one
long-running bridge follow fresh Docker-mapped ports across lab restarts
without needing a bridge restart on every port change.

The runtime also includes preferred Caldera target hosts in the attacker plan.
The bridge uses those hosts when possible to move only the intended live agents
into a temporary per-run group before launching the operation, which keeps a
scenario like `sen4_edge2_clouddb` from also dispatching through unrelated
agents in the default `red` group.

## Dispatch Defender Action

The defender path is:

```text
selected defender strategy -> cloud_policy context/decision -> Ryu REST action
```

To actually apply the selected defender strategy through cloud_policy and Ryu:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --cloud-policy-url http://10.0.10.13:8000/context \
  --execute-defender
```

When `http://10.0.10.13:8000` is not reachable from the host, keep the same
command. The runtime will post from inside `mn.cloud_policy` to the service's
loopback endpoint.

The runtime posts selected strategy context to cloud_policy, asks
`cloud_policy` for `/decide`, then sends the resulting Ryu intent to:

```text
http://127.0.0.1:8080/mtd/action
```

Use `--ryu-action-url` to override it.

## Post Strategy Context To cloud_policy

To make cloud_policy aware of the selected strategies:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --cloud-policy-url http://10.0.10.13:8000/context
```

`cloud_policy` now accepts `strategy_layer`,
`selected_attacker_strategy`, and `selected_defender_strategy` context keys. Its
`/decide` endpoint can echo a strategy-selected defender action as a Ryu intent,
while Ryu execution remains owned by the controller API.

## Stage Transitions

Each runtime call can build a repeated-game transition:

```text
S_t + (a_t, d_t) + observed metrics -> S_(t+1)
```

By default, the runtime observes the next state immediately after execution and
appends a JSONL transition record to:

```text
integrations/strategy/stage_history.jsonl
```

It also writes a compact per-stage decision journal to:

```text
integrations/strategy/decision_trace.jsonl
```

The transition log keeps the full state/execution record. The decision trace is
the faster way to answer "what did attacker and defender choose, why did they
win, and what actually executed?" Each record includes the selected strategy
IDs, utility, population share before and after the replicator update,
Caldera/Ryu execution summaries, and a compact state summary.

Use a delay when the lab needs time for counters to move:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --cloud-policy-url http://10.0.10.13:8000/context \
  --execute-defender \
  --observe-delay-seconds 5
```

When the attacker is dispatched through Caldera, a very short delay only proves
that the operation was accepted. It does not mean the attack_result has already
come back. In this lab the Sandcat agents commonly sleep for 30-60 seconds, so
use a longer delay such as `--observe-delay-seconds 45` to `70`, or rerun the
state check after the Caldera operation reaches `finished` or `cleanup`.

To redirect or suppress the compact journal:

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --decision-trace-log /tmp/decision_trace.jsonl
```

```bash
python integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --no-decision-trace-log
```

For throwaway tests:

```bash
python integrations/strategy/strategy_runtime.py \
  --offline \
  --scenario-id sen4_edge2_clouddb \
  --no-save-population \
  --no-stage-log
```

## Population State

By default, `strategy_runtime.py` stores the evolved mixed populations in:

```text
integrations/strategy/population_state.json
```

Use `--no-save-population` for one-off dry runs, or `--no-population-load` to
start a fresh stage without previous shares.
