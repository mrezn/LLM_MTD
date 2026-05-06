#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker build -t llm-mtd-emo/sensor-node:latest -f "$ROOT_DIR/images/sensor-node/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/edge-gateway:latest -f "$ROOT_DIR/images/edge-gateway/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/edge-worker:latest -f "$ROOT_DIR/images/edge-worker/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-db:latest -f "$ROOT_DIR/images/cloud-db/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-object:latest -f "$ROOT_DIR/images/cloud-object/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-metrics:latest -f "$ROOT_DIR/images/cloud-metrics/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-policy:latest -f "$ROOT_DIR/images/cloud-policy/Dockerfile" "$ROOT_DIR"
docker build -t llm-mtd-emo/cloud-logger:latest -f "$ROOT_DIR/images/cloud-logger/Dockerfile" "$ROOT_DIR"
