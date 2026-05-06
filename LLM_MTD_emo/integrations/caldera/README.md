# Caldera Integration

Keep Caldera outside this repository. A clean lab layout is:

```bash
~/tools/caldera
~/LLM_MTD_emo
```

Start Caldera from its own directory:

```bash
cd ~/tools/caldera
python3 server.py --build
```

Open the GUI:

```text
http://127.0.0.1:8888
```

Use the credentials in:

```text
~/tools/caldera/conf/local.yml
```

The GUI login and the REST API are separate auth flows. A plain API call like:

```bash
curl -i http://127.0.0.1:8888/api/v2/operations
```

returning `401 Unauthorized` is expected unless you send a valid session cookie
or an API key header. For direct API checks, use:

```bash
curl -s -H "KEY: $API_TOKEN" http://127.0.0.1:8888/api/v2/operations
```

The bridge accepts either `CALDERA_API_KEY` or `API_TOKEN`.

## Install These Lab Assets Into Caldera

This repository stores the lab-specific ability and adversary files, but Caldera
loads them from its plugin data directories. Copy them into Stockpile before
starting or restarting Caldera:

```bash
cd ~/LLM_MTD_emo
mkdir -p ~/tools/caldera/plugins/stockpile/data/abilities/llm_mtd_emo/linux
mkdir -p ~/tools/caldera/plugins/stockpile/data/adversaries/llm_mtd_emo
cp integrations/caldera/abilities/linux/*.yml \
  ~/tools/caldera/plugins/stockpile/data/abilities/llm_mtd_emo/linux/
cp integrations/caldera/adversaries/*.yml \
  ~/tools/caldera/plugins/stockpile/data/adversaries/llm_mtd_emo/
```

Then restart Caldera and check that these appear in the GUI:

- Ability: `LLM MTD sensor probe`
- Ability: `LLM MTD controlled edge HTTP telemetry load`
- Ability: `LLM MTD cloud DB probe`
- Adversary: `LLM MTD sensor to edge`
- Adversary: `LLM MTD edge to cloud`
- Adversary: `LLM MTD dual homed sensor path`

## Target Scope

Start with only:

- `mn.sen4`
- `mn.edge2_vm_s4`

Add `mn.cloud_db` later only if you need cloud-side targeting.

## Prepare Target Containers

The repo Dockerfiles for `sensor-node` and `edge-worker` include the utilities
needed for Sandcat and the first abilities:

- `bash`
- `curl`
- `wget`
- `procps`
- `iproute2`

After Dockerfile changes, rebuild and restart the lab:

```bash
cd ~/LLM_MTD_emo
bash scripts/build_images.sh
sudo mn -c
```

Then restart Ryu and the topology in the usual order.

## Enroll Sandcat On sen4 First

In the Caldera GUI:

1. Go to Agents.
2. Deploy an agent.
3. Choose Sandcat.
4. Choose Linux.
5. Set `app.contact.http` to your reachable Caldera URL, for example:

```text
http://<CALDERA_HOST>:8888
```

Copy the Linux deployment command, then run it inside `mn.sen4`:

```bash
sudo docker exec -it mn.sen4 bash
```

Paste the deploy command in that shell.

Pass condition: `mn.sen4` appears as a live agent in Caldera.

Only after that, repeat the same process for:

```bash
sudo docker exec -it mn.edge2_vm_s4 bash
```

## Run The First Manual Operation

Scenario:

```text
sen4 -> edge2_gw -> edge2_vm_s4 -> cloud_db
```

Use:

- Agent: `mn.sen4`
- Adversary: `LLM MTD sensor to edge`
- Mode: manual approval

Approve commands one by one. In parallel, watch:

```bash
sudo docker logs -f mn.cloud_logger
sudo docker exec mn.cloud_metrics curl -s http://127.0.0.1:8000/core
sudo docker exec mn.edge2_gw curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.edge2_vm_s4 curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.cloud_db curl -s http://127.0.0.1:8000/metrics
curl http://127.0.0.1:8080/mtd/metrics
```

The abilities post attack events to:

```text
http://10.0.10.14:8000/attack/event
```

`cloud_logger` writes the JSONL event and forwards it to:

```text
http://10.0.10.12:8000/attack/event
```

That makes the live Caldera run visible in `cloud_metrics`.

## Summarize The Manual Operation

After the manual operation finishes, record one compact result for the first
scenario. Replace the delta values with the values you observed from
`cloud_metrics`, `edge2_gw`, `edge2_vm_s4`, and `cloud_db`:

```bash
cd ~/LLM_MTD_emo
python3 integrations/caldera/caldera_client.py \
  --operation-id sensor_edge_trial_01 \
  --scenario-id sen4_edge2_clouddb \
  --success true \
  --gateway-received-delta 42 \
  --worker-request-delta 39 \
  --cloud-summary-delta 8 \
  --gateway-queue-spike false \
  --logger-url http://10.0.10.14:8000/attack/event \
  --policy-url http://10.0.10.13:8000/context \
  --mulval-policy-json integrations/mulval/outputs/base_edge2_policy.json
```

This writes:

```text
integrations/caldera/results/sensor_edge_trial_01.json
```

It also posts an `attack_result` event to `cloud_logger` and loads the MulVAL
policy JSON plus Caldera result into `cloud_policy` in observe-only mode.

## First MulVAL And Caldera Loop

Use only this first path until it is stable:

```text
sen4 -> edge2_gw -> edge2_vm_s4 -> cloud_db
```

The loop is:

1. Generate the MulVAL graph and parse it:

```bash
cd ~/LLM_MTD_emo/integrations/mulval/outputs/base_edge2_path
graph_gen.sh input.P
cd ~/LLM_MTD_emo
python3 integrations/mulval/assets/parser.py \
  --graph integrations/mulval/outputs/base_edge2_path/AttackGraph.xml \
  --topology integrations/mulval/outputs/topology_auto.json \
  --scenario-id base_edge2_path \
  --output integrations/mulval/outputs/base_edge2_policy.json
```

If MulVAL only emits `AttackGraph.txt`, the parser falls back to the text file
when it sits next to the requested XML path.

2. Run Caldera manually:

```text
Agent: mn.sen4
Adversary: LLM MTD sensor to edge
Mode: manual approval
```

3. Apply one defense:

```bash
curl -X POST http://127.0.0.1:8080/mtd/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"quarantine_sensor","target":"sen4"}'
```

4. Record the defense result if the counters stopped or drop rules became
active:

```bash
python3 integrations/caldera/caldera_client.py \
  --operation-id sensor_edge_trial_01 \
  --scenario-id sen4_edge2_clouddb \
  --success true \
  --gateway-received-delta 42 \
  --worker-request-delta 39 \
  --cloud-summary-delta 8 \
  --gateway-queue-spike false \
  --defense-action quarantine_sensor \
  --defense-target sen4 \
  --defense-success true \
  --drop-rules-active true \
  --counters-stopped true \
  --ovs-drop-counter-delta 12 \
  --logger-url http://10.0.10.14:8000/attack/event \
  --policy-url http://10.0.10.13:8000/context \
  --mulval-policy-json integrations/mulval/outputs/base_edge2_policy.json
```

5. Compare:

```bash
sudo docker exec mn.cloud_metrics curl -s http://127.0.0.1:8000/core
sudo docker exec mn.edge2_gw curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.edge2_vm_s4 curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.cloud_db curl -s http://127.0.0.1:8000/metrics
curl http://127.0.0.1:8080/mtd/metrics
sudo ovs-ofctl dump-flows <edge2_switch_name>
```

Watch for:

- Caldera operation success
- gateway deltas for `sen4`
- worker request deltas on `edge2_vm_s4`
- cloud summary/storage deltas
- Ryu action timing
- OVS drop counters

Do not add cloud compromise, dual-homing, migration, or policy automation until
this first attacker-vs-defender trace is repeatable.
