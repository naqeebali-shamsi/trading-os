#!/usr/bin/env python3
"""
swarm/scripts/orchestrator_pulse.py — v1.0
Deterministic pulse. Checks swarm_state, journal health, equity curve.
Appends heartbeat to daily log.
"""
from datetime import datetime, timezone, timedelta
import os, sqlite3, sys

STATE = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/config/swarm_state"
BROKEN = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/whats_broken.md"
LOG = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/logs"
JOURNAL = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/data/journal.sqlite"

now = datetime.now(timezone.utc)
today = now.strftime("%Y-%m-%d")
log_path = os.path.join(LOG, f"orchestrator-{today}.md")

# ── 1. Read state ──
with open(STATE) as f:
    state = f.read().strip()

if state == "EMERGENCY_HALT":
    print(f"HALT — state={state}")
    sys.exit(1)
if state == "PAUSED":
    print(f"PAUSED — state={state}")
    sys.exit(0)

# ── 2. Journal checks ──
trades_today = 0
pnl_today = 0.0
last_equity = 0.0

try:
    with sqlite3.connect(JOURNAL) as db:
        row = db.execute("SELECT COUNT(*), SUM(pnl) FROM pnl WHERE t LIKE ?", (today + "%",)).fetchone()
        trades_today = row[0] or 0
        pnl_today = row[1] or 0.0
        
        row = db.execute("SELECT equity FROM equity ORDER BY t DESC LIMIT 1").fetchone()
        if row:
            last_equity = row[0]
except Exception as e:
    print(f"Journal error: {e}")

# ── 3. 3-day comparison ──
consecutive_flat = False
if os.path.exists(JOURNAL):
    try:
        with sqlite3.connect(JOURNAL) as db:
            for i in range(1, 4):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                row = db.execute("SELECT SUM(pnl) FROM pnl WHERE t LIKE ?", (d + "%",)).fetchone()
                if row[0] is None or row[0] <= 0:
                    consecutive_flat = True
                else:
                    consecutive_flat = False
                    break
    except:
        pass

# ── 4. Write heartbeat log ──
os.makedirs(LOG, exist_ok=True)
with open(log_path, "a") as f:
    f.write(f"| {now.strftime('%H:%M')} | {state} | trades={trades_today} pnl=${pnl_today:+.2f} equity=${last_equity:,.2f} |\n")

# ── 5. Escalation ──
if consecutive_flat and trades_today == 0:
    with open(BROKEN, "a") as f:
        f.write(f"\n## {now.isoformat()}\n- [ESCALATE] 3+ days no P&L and zero trades today. Strategy may be broken.\n")
    print("ESCALATED")
else:
    print(f"Pulse OK — trades={trades_today} pnl=${pnl_today:+.2f}")
