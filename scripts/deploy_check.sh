#!/bin/bash
# scripts/deploy_check.sh — Pre-flight checks before going live
# Usage: bash scripts/deploy_check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export TRADING_OS_DEPLOY_CHECK_RUNNING=1

if [ -x "$ROOT/venv/Scripts/python.exe" ]; then
  PYTHON="$ROOT/venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

echo "========================================"
echo "  Trading OS — Deploy Check"
echo "========================================"

FAIL=0

# 1. Check critical runtime files exist
echo ""
echo "[1/8] File presence..."
for f in kernel/supervisor.py scripts/readiness_gate.py scripts/real_mode_audit.py; do
  if [ -f "$f" ]; then
    echo "  OK: $f"
  else
    echo "  FAIL: $f missing!"
    FAIL=1
  fi
done

# 2. Python syntax check
echo ""
echo "[2/8] Syntax check..."
if "$PYTHON" -m py_compile kernel/supervisor.py kernel/hooks.py muscle/multisymbol_router.py muscle/muscle_main.py muscle/pnl_sync.py sensory/combined_feed.py sensory/timesfm_forecaster.py sensory/candle_patterns.py cortex/news_orchestrator.py cortex/event_radar.py cortex/instrument_registry.py cortex/decision_guard.py cortex/llm_client.py cortex/agent_schemas.py cortex/agent.py cortex/main.py rd/dream_scheduler.py rd/agents/explorer.py research/strategy_search/engine.py introspect/score_strategies.py introspect/decision_evaluator.py introspect/ensemble_reviewer.py telemetry/metrics.py scripts/readiness_gate.py scripts/run_strategy_search.py scripts/brain_smoke.py scripts/real_mode_audit.py scripts/opportunity_scanner.py scripts/verify_instruments.py; then
  echo "  OK: all files compile"
else
  echo "  FAIL: syntax error"
  FAIL=1
fi

# 3. Check IPC directory writable
echo ""
echo "[3/8] IPC directory..."
mkdir -p "$ROOT/ipc"
touch "$ROOT/ipc/.write_test" && rm "$ROOT/ipc/.write_test"
echo "  OK: IPC dir writable"

# 4. Check chart directories (or warn if none)
echo ""
echo "[4/8] Chart directories..."
CHARTS=$(find "$ROOT/ipc" -maxdepth 1 -type d -name 'chart_*' 2>/dev/null || true)
if [ -z "$CHARTS" ]; then
  echo "  WARN: no chart_* dirs found (MT5 not running yet, expected before go-live)"
else
  echo "  OK: found charts:"
  for c in $CHARTS; do
    echo "    - $(basename $c)"
  done
fi

# 5. Check for STOP_TRADING
echo ""
echo "[5/8] STOP_TRADING..."
if [ -f "$ROOT/STOP_TRADING" ]; then
  echo "  FAIL: STOP_TRADING file exists! Remove before trading."
  FAIL=1
else
  echo "  OK: no STOP_TRADING flag"
fi

# 6. Check strategy registry
echo ""
echo "[6/8] Strategy registry..."
if [ -f "$ROOT/cortex/strategies.json" ]; then
  if "$PYTHON" -c "import json; json.load(open('$ROOT/cortex/strategies.json'))"; then
    echo "  OK: strategies.json valid"
  else
    echo "  FAIL: strategies.json is invalid JSON"
    FAIL=1
  fi
else
  echo "  WARN: strategies.json not found"
fi

# 7. Check bus system
echo ""
echo "[7/8] Nervous bus..."
mkdir -p "$ROOT/nervous/topics"
touch "$ROOT/nervous/bus.jsonl"
echo "  OK: bus directory ready"

# 8. Test suite
echo ""
echo "[8/8] Test suite..."
if ! "$PYTHON" -m pytest --version >/dev/null 2>&1; then
  echo "  FAIL: pytest not installed"
  echo "  Hint: pip install -r requirements-dev.txt"
  FAIL=1
else
  "$PYTHON" -m pytest tests/ -q || {
    echo "  FAIL: pytest suite failed"
    FAIL=1
  }
fi
"$PYTHON" scripts/real_mode_audit.py --strict || {
  echo "  FAIL: demo/live real-mode audit failed"
  FAIL=1
}
"$PYTHON" scripts/readiness_gate.py || {
  echo "  FAIL: readiness gate failed"
  FAIL=1
}

echo ""
echo "========================================"
if [ $FAIL -eq 0 ]; then
  echo "  ALL CHECKS PASSED — READY FOR GO-LIVE"
  echo "========================================"
  echo ""
  echo "Boot command:"
  echo "  cd $ROOT && $PYTHON kernel/supervisor.py"
  exit 0
else
  echo "  SOME CHECKS FAILED — DO NOT GO-LIVE"
  echo "========================================"
  exit 1
fi
