#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker build -t llm-mtd-emo/sensor-node:latest -f "$ROOT_DIR/environment/services/sensor-node/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/edge-gateway:latest -f "$ROOT_DIR/environment/services/edge-gateway/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/edge-worker:latest -f "$ROOT_DIR/environment/services/edge-worker/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-db:latest -f "$ROOT_DIR/environment/services/cloud-db/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-object:latest -f "$ROOT_DIR/environment/services/cloud-object/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-metrics:latest -f "$ROOT_DIR/environment/services/cloud-metrics/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-policy:latest -f "$ROOT_DIR/environment/services/cloud-policy/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-logger:latest -f "$ROOT_DIR/environment/services/cloud-logger/Dockerfile" "$ROOT_DIR"
