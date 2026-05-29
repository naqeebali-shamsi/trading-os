#!/usr/bin/env python3
"""
trade_executor.py — stdlib-only. Reads approved signals from executor INBOX.
Validates against runtime_state + config risk limits.
Executes via bridge in PAPER or LIVE mode.
Writes trade_journal.csv and runtime_state.json.
"""
import os, sys, json, csv, logging
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = Path("/mnt/e/GROWTH/trading-os")
CONFIG = json.loads((WORKSPACE / "config" / "swarm-config.json").read_text())
RUNTIME_FILE = WORKSPACE / "config" / "runtime_state.json"
EXECUTOR_INBOX = WORKSPACE / "queue" / "trade-executor" / "INBOX.md"
EXECUTOR_OUTBOX = WORKSPACE / "queue" / "trade-executor" / "OUTBOX.md"
JOURNAL = WORKSPACE / "intel" / "trade_journal.csv"
BRIDGE_PATH = WORKSPACE / "bridge"
sys.path.insert(0, str(BRIDGE_PATH))
from mt5_ipc_engine import MT5IPCBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s | executor | %(message)s", stream=sys.stderr)
logger = logging.getLogger("executor")

def load_runtime():
    try: return json.loads(RUNTIME_FILE.read_text())
    except: return {"orders_today": 0, "last_order_time": None, "positions_opened_today": 0}

def save_runtime(data):
    RUNTIME_FILE.write_text(json.dumps(data, indent=2))

def parse_tasks(markdown):
    tasks = []
    for block in markdown.split("##"):
        if "Task:" not in block and "ORDER" not in block and "signal:" not in block: continue
        t = {"symbol": None, "side": None, "volume": None, "sl": 0.0, "tp": 0.0, "comment": "os_auto"}
        for line in block.splitlines():
            if line.startswith("### Signal:"):
                parts = line.replace("### Signal:", "").strip().split()
                if len(parts) >= 4:
                    t["symbol"] = parts[0]
                    t["side"] = parts[-1].split(":")[-1].lower() if ":" in parts[-1] else parts[-1].lower()
            if line.startswith("### Volume:"): t["volume"] = float(line.split(":")[-1].strip())
            if line.startswith("### SL:"): t["sl"] = float(line.split(":")[-1].strip())
            if line.startswith("### TP:"): t["tp"] = float(line.split(":")[-1].strip())
            if line.startswith("### Comment:"): t["comment"] = line.split(":", 1)[1].strip()
        if t["symbol"] and t["side"] and t["volume"]:
            tasks.append(t)
    return tasks

def throttle_ok(rt):
    cfg_ex = CONFIG["executor"]
    if rt.get("orders_today", 0) >= CONFIG["executor"]["max_orders_per_day"]: return False, "daily_max"
    if rt.get("last_order_time"):
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(rt["last_order_time"])).total_seconds()
        if elapsed < cfg_ex["minimum_seconds_between_actions"]: return False, "throttled"
    return True, ""

def execute_task(bridge, task):
    mode = CONFIG.get("mode", "PAPER")
    if mode == "PAPER":
        logger.info("[PAPER] Order: %s %s %.2f lots", task["side"].upper(), task["symbol"], task["volume"])
        return {"status": "paper", "ticket": "paper_" + str(int(datetime.now(timezone.utc).timestamp()))}
    r = bridge.place_order(task["symbol"], task["volume"], task["side"], task.get("sl", 0.0), task.get("tp", 0.0), task.get("comment", "os_auto"))
    logger.info("[LIVE] Order result: %s", r)
    return r

def main():
    if CONFIG.get("state", "ACTIVE") != "ACTIVE":
        logger.info("State not ACTIVE; idle."); return
    md = EXECUTOR_INBOX.read_text() if EXECUTOR_INBOX.exists() else ""
    if "##" not in md: logger.info("No pending executor tasks."); return
    tasks = parse_tasks(md)
    if not tasks: logger.info("No actionable tasks found."); return
    bridge = MT5IPCBridge(WORKSPACE)
    h = bridge.health()
    if not h.get("connected"):
        logger.warning("Bridge down: %s", h); return
    rt = load_runtime()
    fieldnames = ["timestamp", "symbol", "side", "volume", "sl", "tp", "ticket", "status", "pnl", "strategy", "mode", "comment"]
    journal_exists = JOURNAL.exists()
    with open(JOURNAL, "a", newline="") as jf:
        writer = csv.DictWriter(jf, fieldnames=fieldnames)
        if not journal_exists: writer.writeheader()
        with open(EXECUTOR_OUTBOX, "a") as out:
            for task in tasks:
                ok, reason = throttle_ok(rt)
                if not ok:
                    out.write(f"\n## Rejected | {datetime.now(timezone.utc).isoformat()}\n")
                    out.write(f"**Reason**: {reason}\n")
                    logger.info("Task rejected: %s", reason); continue
                result = execute_task(bridge, task)
                row = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": task["symbol"], "side": task["side"].upper(),
                    "volume": task.get("volume"), "sl": task.get("sl", 0.0), "tp": task.get("tp", 0.0),
                    "ticket": result.get("ticket", ""), "status": result.get("status", "unknown"),
                    "pnl": "", "strategy": "", "mode": CONFIG.get("mode", "PAPER"), "comment": task.get("comment", "")
                }
                writer.writerow(row)
                out.write(f"\n## Executed | {datetime.now(timezone.utc).isoformat()}\n")
                out.write(f"**Symbol**: {task['symbol']} | **Side**: {task['side']} | **Volume**: {task['volume']}\n")
                out.write(f"**Result**: {json.dumps(result)}\n")
                rt["orders_today"] = rt.get("orders_today", 0) + 1
                rt["positions_opened_today"] = rt.get("positions_opened_today", 0) + 1
                rt["last_order_time"] = datetime.now(timezone.utc).isoformat()
    save_runtime(rt)
    logger.info("Executor done. %d tasks processed.", len(tasks))

if __name__ == "__main__":
    main()
