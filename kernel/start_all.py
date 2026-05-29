#!/usr/bin/env python3
"""
kernel/start_all.py -- One-command OS Boot
------------------------------------------
Uses kernel/supervisor.py to start all 14 biological layers,
manage their lifecycle, and keep the system alive.

Layers:
  Sensory    (market data, regime, calendar)
  Muscle     (order routing, positions)
  Immune     (risk gate, anomaly detection)
  Memory     (journal, equity curve, embeddings)
  Cortex     (LLM brain, strategy, decisions)
  Swarm      (R&D subagents: backtest, code-gen, safety-review)
  Consciousness (dashboard at :8765, alert router)

Usage:
  python3 kernel/start_all.py        # Start
  Ctrl+C                             # Graceful stop
"""
import subprocess, sys, pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from kernel.supervisor import run

if __name__ == "__main__":
    run()
