#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/build.sh host
# The manager is the only script the gateway ships; its conformance runs in test_gateway.
node --check device/manager/manager.js
python3 -m unittest discover -s tests/conformance -p 'test_*.py' -v
