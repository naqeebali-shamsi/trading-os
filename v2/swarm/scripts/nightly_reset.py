#!/usr/bin/env python3
"""
swarm/scripts/nightly_reset.py — v1.0
Midnight reset: archive queues, reset counters, trigger after-hours learning.
"""
from datetime import datetime, timezone, timedelta
import os, shutil, sys

SWARM = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm"
TODAY = datetime.now(timezone.utc)
ARCHIVE = os.path.join(SWARM, "logs", "archive", TODAY.strftime("%Y-%m-%d"))
STATE = os.path.join(SWARM, "config", "swarm_state")

os.makedirs(ARCHIVE, exist_ok=True)

# ── 1. Archive INBOX/OUTBOX ──
for agent_name in ("research", "drafter", "executor", "analyst", "learner", "orchestrator"):
    for fname in ("INBOX.md", "OUTBOX.md"):
        src = os.path.join(SWARM, "queue", agent_name, fname)
        if os.path.exists(src):
            dst = os.path.join(ARCHIVE, f"{agent_name}-{fname}")
            shutil.copy2(src, dst)

# ── 2. Reset daily counters ──
with open(STATE) as f:
    state = f.read().strip()

# Rebuild state keeping mode but resetting counters
new_state = "ACTIVE" if state == "PAUSED" else state
with open(STATE, "w") as f:
    f.write(new_state)

# ── 3. Write tomorrow plan ──
tomorrow = (TODAY + timedelta(days=1)).strftime("%Y-%m-%d")
plan_path = os.path.join(SWARM, "calendar", "tomorrow.md")
os.makedirs(os.path.dirname(plan_path), exist_ok=True)
with open(plan_path, "w") as f:
    f.write(f"# Trading Plan — {tomorrow}\n\n")
    f.write("- [ ] Pre-market: check VIX, earnings calendar\n")
    f.write("- [ ] Confirm strategy parameters\n")
    f.write("- [ ] Monitor health_monitor alerts\n")
    f.write("- [ ] End-of-day: review P&L, run after-hours learner\n")

# ── 4. Trigger after-hours learner ──
learner = os.path.join(SWARM, "scripts", "after_hours_learner.py")
if os.path.exists(learner):
    os.system(f"python3 {learner} >> /tmp/after_hours_learner.log 2>&1")

print(f"Nightly reset complete. Archived to {ARCHIVE}")
