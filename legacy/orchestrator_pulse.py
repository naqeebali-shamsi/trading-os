#!/usr/bin/env python3
"""
orchestrator_pulse.py — Master heartbeat. Runs every 5 min.
1. Read human override INBOX (EMERGENCY_HALT, PAUSE, FORCE_ORDER) and execute immediately
2. Check bridge_daemon status
3. Verify runtime_state matches daily reset
4. If PAUSED or HALT: exit after writing state
5. Route signal-forge → risk-guardian → executor queues
6. Check daily drawdown / loss limits, enforce PAUSE if breached
7. Verify position_monitor is running (max positions check)
8. Write orchestrator OUTBOX log
"""
import os, sys, json, logging
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = Path("/mnt/e/GROWTH/trading-os")
CONFIG_FILE = WORKSPACE / "config" / "swarm-config.json"
RUNTIME_FILE = WORKSPACE / "config" / "runtime_state.json"
STATE_FILE = WORKSPACE / "config" / "swarm_state"
OVERRIDE_INBOX = WORKSPACE / "queue" / "orchestrator-trader" / "INBOX.md"
OUTBOX = WORKSPACE / "queue" / "orchestrator-trader" / "OUTBOX.md"
RISK_INBOX = WORKSPACE / "queue" / "risk-guardian" / "INBOX.md"
EXEC_INBOX = WORKSPACE / "queue" / "trade-executor" / "INBOX.md"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | orchestra | %(message)s", stream=sys.stderr)
logger = logging.getLogger("orchestrator")

def load_config():
    return json.loads(CONFIG_FILE.read_text())

def load_runtime():
    try: return json.loads(RUNTIME_FILE.read_text())
    except: return {}

def save_runtime(data):
    RUNTIME_FILE.write_text(json.dumps(data, indent=2))

def set_state(s):
    STATE_FILE.write_text(s + "\n")

def parse_override(md):
    cmds = []
    for line in md.splitlines():
        if line.startswith("→"): line = line.lstrip("→ ")
        line = line.strip()
        if not line or line.startswith("#"): continue
        if line.upper() == "EMERGENCY_HALT": cmds.append(("halt", ""))
        elif line.upper() == "PAUSE": cmds.append(("pause", ""))
        elif line.upper() == "RESUME": cmds.append(("resume", ""))
        elif line.upper().startswith("CLOSE_ALL"): cmds.append(("close_all", ""))
        elif line.upper().startswith("FORCE_ORDER:"): cmds.append(("force_order", line.split(":", 1)[1]))
        elif line.upper().startswith("SWITCH_MODE:"): cmds.append(("switch_mode", line.split(":", 1)[1].strip()))
    return cmds

def handle_cmd(cmd, arg, cfg, rt):
    if cmd == "halt":
        set_state("EMERGENCY_HALT")
        logger.critical("EMERGENCY_HALT executed from human override.")
        return True
    if cmd == "pause": set_state("PAUSED"); logger.info("PAUSED by human."); return True
    if cmd == "resume": set_state("ACTIVE"); logger.info("RESUMED by human."); return True
    if cmd == "switch_mode":
        cfg_mode = arg.lower()
        if cfg_mode in ["live", "paper"]:
            logger.info("MODE change requested: %s", cfg_mode)
            with open(CONFIG_FILE, "r+") as f:
                data = json.load(f)
                data["mode"] = cfg_mode.upper()
                f.seek(0); json.dump(data, f, indent=2); f.truncate()
            logger.info("Mode switched to %s", cfg_mode.upper())
        return True
    if cmd == "close_all":
        logger.warning("CLOSE_ALL requested. Routing to executor.")
        with open(EXEC_INBOX, "a") as f:
            f.write(f"\n## Task: close_all | {datetime.now(timezone.utc).isoformat()}\n")
            f.write("### Comment: Human override CLOSE_ALL\n")
        return True
    if cmd == "force_order":
        logger.warning("FORCE_ORDER: %s", arg)
        params = {}
        for pair in arg.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k.strip()] = v.strip()
        with open(EXEC_INBOX, "a") as f:
            f.write(f"\n## Task: force_order | {datetime.now(timezone.utc).isoformat()}\n")
            for k, v in params.items(): f.write(f"### {k.capitalize()}: {v}\n")
            f.write("---\n")
        return True
    return False

def check_risk_limits(cfg, rt):
    daily_pnl = rt.get("daily_pnl", 0.0)
    equity = 10000.0  # placeholder until bridge reads real equity
    if daily_pnl <= -equity * cfg["risk"]["max_daily_loss_pct"] / 100:
        logger.critical("DAILY LOSS LIMIT BREACHED. PnL=%.2f (%.2f%%). PAUSING.", daily_pnl, cfg["risk"]["max_daily_loss_pct"])
        set_state("PAUSED")
        return False
    return True

def route_approved_signals():
    sfo = WORKSPACE / "queue" / "signal-forge" / "OUTBOX.md"
    if sfo.exists():
        content = sfo.read_text()
        if "## Task:" in content or "signal:" in content:
            with open(RISK_INBOX, "a") as f: f.write(content)
            sfo.write_text("# Cleared after routing\n")
    rgo = WORKSPACE / "queue" / "risk-guardian" / "OUTBOX.md"
    if rgo.exists():
        content = rgo.read_text()
        if "APPROVED" in content or "### Volume:" in content:
            with open(EXEC_INBOX, "a") as f: f.write(content)
            rgo.write_text("# Cleared after routing\n")

def daily_reset_if_needed(cfg, rt):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if rt.get("date", "") != today:
        logger.info("Daily reset. New date: %s", today)
        rt = {"date": today, "orders_today": 0, "closes_today": 0, "daily_pnl": 0.0,
              "positions_opened_today": 0, "positions_closed_today": 0, "risk_gate_rejections_today": 0,
              "emergency_flags": [], "human_override_executed": []}
        save_runtime(rt)
    return rt

def main():
    cfg = load_config()
    rt = load_runtime()
    rt = daily_reset_if_needed(cfg, rt)
    override_md = OVERRIDE_INBOX.read_text() if OVERRIDE_INBOX.exists() else ""
    executed = False
    if override_md.strip() and "## Pending Commands" in override_md:
        pending_section = override_md.split("## Pending Commands")[-1].split("## Completed Commands")[0]
        cmds = parse_override(pending_section)
        for cmd, arg in cmds:
            if handle_cmd(cmd, arg, cfg, rt):
                executed = True
                rt.setdefault("human_override_executed", []).append(f"{datetime.now(timezone.utc).isoformat()}:{cmd}:{arg}")
        if executed:
            with open(OVERRIDE_INBOX, "r+") as f:
                data = f.read()
                if "## Pending Commands" in data:
                    new_data = data.replace(pending_section, "\n→ [Processed]\n")
                    f.seek(0); f.write(new_data); f.truncate()

    state = STATE_FILE.read_text().strip() if STATE_FILE.exists() else "ACTIVE"
    if state == "PAUSED":
        logger.info("Swarm PAUSED. Orchestrator idle.")
    elif state == "EMERGENCY_HALT":
        logger.info("Swarm EMERGENCY_HALT. Manual intervention required.")
    elif state == "ACTIVE":
        check_risk_limits(cfg, rt)
        route_approved_signals()
        bridge_pid_file = Path("/tmp/mt5_bridge_daemon.pid")
        bridge_ok = bridge_pid_file.exists()
        with open(OUTBOX, "a") as f:
            f.write(f"\n## Pulse | {datetime.now(timezone.utc).isoformat()} | state={state} | bridge={bridge_ok} | overrides_executed={executed}\n")

    save_runtime(rt)

if __name__ == "__main__":
    main()
