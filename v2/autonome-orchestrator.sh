#!/usr/bin/env bash
# autonome-orchestrator.sh — Unified launcher for v2.1 intelligence layer
#
# Usage:
#   ./autonome-orchestrator.sh start    # Start all services
#   ./autonome-orchestrator.sh stop     # Stop all services
#   ./autonome-orchestrator.sh status   # Check status
#   ./autonome-orchestrator.sh dream    # Run DreamPod once (pre-market)
#   ./autonome-orchestrator.sh discover # Run Discovery once
#   ./autonome-orchestrator.sh gate-test # Test LLM Gate with a fake signal
#

set -euo pipefail

ROOT="/mnt/e/NomadCrew[GROWTH]/trading-os/v2"
cd "$ROOT"

VENV="${ROOT}/.venv/bin/activate"
if [ -f "$VENV" ]; then
    source "$VENV" 2>/dev/null || true
fi

case "${1:-status}" in
    start)
        echo "[+] Starting Autonome v2.1 intelligence layer..."
        systemctl --user daemon-reload
        systemctl --user start autonome-dreampod.timer
        systemctl --user start autonome-discovery.timer
        systemctl --user start autonome-review.timer
        systemctl --user start autonome-supervisor.service
        echo "[+] All services started. Status:"
        systemctl --user status autonome-supervisor.service --no-pager || true
        ;;
    stop)
        echo "[-] Stopping all Autonome services..."
        systemctl --user stop autonome-supervisor.service 2>/dev/null || true
        systemctl --user stop autonome-dreampod.timer 2>/dev/null || true
        systemctl --user stop autonome-discovery.timer 2>/dev/null || true
        systemctl --user stop autonome-review.timer 2>/dev/null || true
        pkill -f autonome.supervisor.main 2>/dev/null || true
        echo "[-] All stopped."
        ;;
    status)
        echo "=== Autonome v2.1 Service Status ==="
        for svc in autonome-supervisor autonome-dreampod.timer autonome-discovery.timer autonome-review.timer; do
            status=$(systemctl --user is-active "$svc" 2>/dev/null || echo "unknown")
            printf "  %-30s %s\n" "$svc:" "$status"
        done
        echo ""
        echo "=== Recent Journal Log ==="
        journalctl --user -u autonome-supervisor -n 5 --no-pager 2>/dev/null || echo "No logs"
        ;;
    dream)
        echo "[+] Running DreamPod now..."
        python3 -m autonome.intelligence.dreampod
        ;;
    discover)
        echo "[+] Running Discovery Engine now..."
        python3 -m autonome.intelligence.discovery
        ;;
    gate-test)
        echo "[+] Testing LLM Gate with synthetic signal..."
        python3 -c "
import sys
sys.path.insert(0, '$ROOT')
from autonome.intelligence.llm_gate import LLMGate, SignalContext
gate = LLMGate()
ctx = SignalContext(
    symbol='SPY', direction='LONG', entry_price=550.0,
    stop_loss=540.0, take_profit=570.0, confidence=0.75,
    strategy='momentum_breakout', regime='uptrend', sector='ETF',
    catalyst='breakout above 20EMA on volume surge'
)
decision = gate.review(ctx)
print(f'Decision: {decision.decision}')
print(f'Confidence: {decision.confidence}')
print(f'Reasoning: {decision.reasoning}')
"
        ;;
    logs)
        journalctl --user -u autonome-supervisor -f
        ;;
    *)
        echo "Usage: $0 {start|stop|status|dream|discover|gate-test|logs}"
        exit 1
        ;;
esac
