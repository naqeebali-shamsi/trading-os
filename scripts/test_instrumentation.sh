#!/bin/bash
# Run end-to-end observability/instrumentation validation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 tests/test_observability.py
python3 tests/test_instrument_registry.py
python3 tests/test_llm_client.py
python3 tests/test_hooks.py
python3 tests/test_agent_schemas.py
python3 tests/test_agent_brain.py
python3 tests/test_cortex_brain_integration.py
python3 tests/test_brain_smoke.py
python3 tests/test_decision_guard.py
python3 tests/test_readiness.py
python3 tests/test_citadel.py
bash scripts/deploy_check.sh

git status --short --ignored
