#!/usr/bin/env python3
"""
swarm/scripts/health_monitor.py — v1.0
Deterministic health watcher. Reads supervisor log + journal.
Writes alerts to intel/whats_broken.md. Sets swarm_state on CRITICAL.
"""
from datetime import datetime, timezone, timedelta
import os, re, sqlite3, json, sys

STATE = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/config/swarm_state"
BROKEN = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/whats_broken.md"
LOG = "/tmp/autonome_paper.log"
JOURNAL = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/data/journal.sqlite"

now = datetime.now(timezone.utc)
issues = []

# ── 1. Supervisor log health ──
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()[-500:]
    
    # Check for CRITICAL / ERROR in last 5 min
    for line in lines:
        m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
        if m:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if (now - t) < timedelta(minutes=5):
                if "CRITICAL" in line or "FATAL" in line:
                    issues.append(f"[CRITICAL] {line.strip()}")
                elif "ERROR" in line and "Failed to query journal" not in line:
                    issues.append(f"[ERROR] {line.strip()}")

    # Check staleness: no heartbeat in 6 minutes
    last_heartbeat = None
    for line in reversed(lines):
        if "Equity=" in line or "heartbeat" in line.lower() or "signal" in line.lower():
            m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
            if m:
                last_heartbeat = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                break
    
    if last_heartbeat and (now - last_heartbeat) > timedelta(minutes=6):
        issues.append(f"[STALE] No heartbeat since {last_heartbeat.isoformat()}")
else:
    issues.append("[MISSING] supervisor log not found")

# ── 2. Journal health ──
if os.path.exists(JOURNAL):
    try:
        with sqlite3.connect(JOURNAL) as db:
            # Check trades today
            today = now.strftime("%Y-%m-%d")
            row = db.execute("SELECT COUNT(*) FROM pnl WHERE t LIKE ?", (today + "%",)).fetchone()
            trades_today = row[0]
            
            # Check drawdown from last equity log
            row = db.execute("SELECT equity, drawdown FROM equity ORDER BY t DESC LIMIT 1").fetchone()
            if row:
                equity, dd = row
                if dd and dd > 5.0:
                    issues.append(f"[DRAWDOWN] Current drawdown {dd:.1f}% > 5%")
            
            # No trades in 4h during market hours?
            if 14 <= now.hour <= 20:  # ~9:30-16:00 ET in UTC
                row = db.execute(
                    "SELECT COUNT(*) FROM pnl WHERE t > ?",
                    ((now - timedelta(hours=4)).isoformat(),)
                ).fetchone()
                if row[0] == 0:
                    issues.append("[IDLE] No trades in 4h during market hours")
    except Exception as e:
        issues.append(f"[JOURNAL_ERR] {e}")

# ── 3. Write issues ──
if issues:
    with open(BROKEN, "a") as f:
        f.write(f"\n## {now.isoformat()}\n")
        for i in issues:
            f.write(f"- {i}\n")
    
    # CRITICAL → halt
    critical = any("[CRITICAL]" in i or "[DRAWDOWN]" in i for i in issues)
    if critical:
        with open(STATE, "w") as f:
            f.write("EMERGENCY_HALT")
        print(f"CRITICAL HALT at {now.isoformat()}")
        sys.exit(1)
    
    # STALE → pause
    if any("[STALE]" in i for i in issues):
        with open(STATE, "w") as f:
            f.write("PAUSED")
        print(f"PAUSED at {now.isoformat()}")
        sys.exit(2)
    
    print(f"Issues logged: {len(issues)}")
else:
    print("Healthy")
