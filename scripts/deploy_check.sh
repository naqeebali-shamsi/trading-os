#!/bin/bash
# scripts/deploy_check.sh — Pre-flight checks before going live
# Usage: bash scripts/deploy_check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export TRADING_OS_DEPLOY_CHECK_RUNNING=1

echo "========================================"
echo "  Trading OS — Deploy Check"
echo "========================================"

FAIL=0

# 1. Check critical files exist
echo ""
echo "[1/8] File presence..."
for f in kernel/supervisor.py kernel/hooks.py muscle/multisymbol_router.py muscle/muscle_main.py muscle/pnl_sync.py sensory/combined_feed.py sensory/timesfm_forecaster.py cortex/news_orchestrator.py cortex/event_radar.py cortex/instrument_registry.py cortex/decision_guard.py cortex/llm_client.py cortex/agent_schemas.py cortex/agent.py cortex/main.py introspect/score_strategies.py introspect/decision_evaluator.py introspect/ensemble_reviewer.py telemetry/metrics.py scripts/readiness_gate.py scripts/brain_smoke.py scripts/real_mode_audit.py scripts/opportunity_scanner.py scripts/opportunity_scanner.ps1 scripts/verify_instruments.py tests/test_citadel.py tests/test_readiness.py tests/test_instrument_registry.py tests/test_decision_guard.py tests/test_llm_client.py tests/test_hooks.py tests/test_agent_schemas.py tests/test_agent_brain.py tests/test_cortex_brain_integration.py tests/test_brain_smoke.py tests/test_runtime_safety.py tests/test_bus_concurrency.py tests/test_dual_chart.py tests/test_ea_no_trade_handlers.py tests/test_live_safety_soak.py tests/test_mt5_ipc_protocol.py tests/test_observability.py tests/test_strategy_consistency.py tests/test_timesfm_forecaster.py tests/test_event_radar.py tests/test_ops_status_learning.py tests/test_market_intelligence_ingestion.py tests/test_live_intelligence_wiring.py tests/test_real_mode_audit.py tests/test_opportunity_scanner.py tests/test_decision_evaluator.py tests/test_ensemble_reviewer.py tests/test_signal_generator_v2.py tests/test_verify_instruments.py; do
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
python3 -m py_compile kernel/supervisor.py kernel/hooks.py muscle/multisymbol_router.py muscle/muscle_main.py muscle/pnl_sync.py sensory/combined_feed.py sensory/timesfm_forecaster.py cortex/news_orchestrator.py cortex/event_radar.py cortex/instrument_registry.py cortex/decision_guard.py cortex/llm_client.py cortex/agent_schemas.py cortex/agent.py cortex/main.py introspect/score_strategies.py introspect/decision_evaluator.py introspect/ensemble_reviewer.py telemetry/metrics.py scripts/readiness_gate.py scripts/brain_smoke.py scripts/real_mode_audit.py scripts/opportunity_scanner.py scripts/verify_instruments.py || {
  echo "  FAIL: syntax error"
  FAIL=1
}
echo "  OK: all files compile"

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
  python3 -c "import json; json.load(open('$ROOT/cortex/strategies.json'))" || {
    echo "  FAIL: strategies.json is invalid JSON"
    FAIL=1
  }
  echo "  OK: strategies.json valid"
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
python3 tests/test_citadel.py || {
  echo "  FAIL: citadel test suite failed"
  FAIL=1
}
python3 tests/test_instrument_registry.py || {
  echo "  FAIL: instrument registry test suite failed"
  FAIL=1
}
python3 tests/test_decision_guard.py || {
  echo "  FAIL: decision guard test suite failed"
  FAIL=1
}
python3 tests/test_llm_client.py || {
  echo "  FAIL: llm client test suite failed"
  FAIL=1
}
python3 tests/test_hooks.py || {
  echo "  FAIL: hooks test suite failed"
  FAIL=1
}
python3 tests/test_agent_schemas.py || {
  echo "  FAIL: agent schemas test suite failed"
  FAIL=1
}
python3 tests/test_agent_brain.py || {
  echo "  FAIL: agent brain test suite failed"
  FAIL=1
}
python3 tests/test_cortex_brain_integration.py || {
  echo "  FAIL: cortex brain integration test suite failed"
  FAIL=1
}
python3 tests/test_brain_smoke.py || {
  echo "  FAIL: brain smoke CLI test suite failed"
  FAIL=1
}
python3 tests/test_readiness.py || {
  echo "  FAIL: readiness test suite failed"
  FAIL=1
}
python3 tests/test_runtime_safety.py || {
  echo "  FAIL: runtime safety test suite failed"
  FAIL=1
}
python3 tests/test_bus_concurrency.py || {
  echo "  FAIL: bus concurrency test suite failed"
  FAIL=1
}
python3 tests/test_dual_chart.py || {
  echo "  FAIL: dual chart smoke test suite failed"
  FAIL=1
}
python3 tests/test_ea_no_trade_handlers.py || {
  echo "  FAIL: EA no-trade static safety test suite failed"
  FAIL=1
}
python3 tests/test_live_safety_soak.py || {
  echo "  FAIL: live safety soak test suite failed"
  FAIL=1
}
python3 tests/test_mt5_ipc_protocol.py || {
  echo "  FAIL: MT5 IPC protocol test suite failed"
  FAIL=1
}
python3 tests/test_observability.py || {
  echo "  FAIL: observability instrumentation test suite failed"
  FAIL=1
}
python3 tests/test_strategy_consistency.py || {
  echo "  FAIL: strategy consistency test suite failed"
  FAIL=1
}
python3 tests/test_timesfm_forecaster.py || {
  echo "  FAIL: TimesFM forecaster test suite failed"
  FAIL=1
}
python3 tests/test_event_radar.py || {
  echo "  FAIL: Event Radar test suite failed"
  FAIL=1
}
python3 tests/test_ops_status_learning.py || {
  echo "  FAIL: ops status/learning test suite failed"
  FAIL=1
}
	python3 tests/test_market_intelligence_ingestion.py || {
	  echo "  FAIL: market intelligence ingestion test suite failed"
	  FAIL=1
	}
	python3 tests/test_live_intelligence_wiring.py || {
	  echo "  FAIL: live intelligence wiring test suite failed"
	  FAIL=1
	}
		python3 tests/test_real_mode_audit.py || {
		  echo "  FAIL: real mode audit test suite failed"
		  FAIL=1
		}
		python3 tests/test_opportunity_scanner.py || {
		  echo "  FAIL: opportunity scanner test suite failed"
		  FAIL=1
		}
		python3 tests/test_decision_evaluator.py || {
		  echo "  FAIL: decision evaluator test suite failed"
		  FAIL=1
		}
		python3 tests/test_ensemble_reviewer.py || {
		  echo "  FAIL: ensemble reviewer test suite failed"
		  FAIL=1
		}
		python3 tests/test_signal_generator_v2.py || {
		  echo "  FAIL: signal generator v2 test suite failed"
		  FAIL=1
		}
		python3 tests/test_verify_instruments.py || {
		  echo "  FAIL: instrument verifier test suite failed"
		  FAIL=1
		}
		python3 scripts/real_mode_audit.py --strict || {
	  echo "  FAIL: demo/live real-mode audit failed"
	  FAIL=1
	}
python3 scripts/readiness_gate.py || {
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
  echo "  cd $ROOT && python3 kernel/supervisor.py"
  exit 0
else
  echo "  SOME CHECKS FAILED — DO NOT GO-LIVE"
  echo "========================================"
  exit 1
fi
