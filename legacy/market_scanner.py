#!/usr/bin/env python3
"""
market_scanner.py — stdlib-only. Reads config/swarm-config.json.
Fetches OHLCV and computes RSI/SMA/ATR via bridge.
Writes snapshots to intel/market_snapshots/.
"""
import os, sys, json, logging
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = Path("/mnt/e/GROWTH/trading-os")
CONFIG = json.loads((WORKSPACE / "config" / "swarm-config.json").read_text())
OUTBOX = WORKSPACE / "queue" / "market-scanner" / "OUTBOX.md"
SNAP_DIR = WORKSPACE / "intel" / "market_snapshots"
SIGNAL_INBOX = WORKSPACE / "queue" / "signal-forge" / "INBOX.md"
BRIDGE_PATH = WORKSPACE / "bridge"
sys.path.insert(0, str(BRIDGE_PATH))
from mt5_ipc_engine import MT5IPCBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s | scanner | %(message)s", stream=sys.stderr)
logger = logging.getLogger("scanner")

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[-i] - closes[-i-1]
        if ch > 0: gains += ch
        else: losses += abs(ch)
    if not losses: return 100.0
    return 100.0 - (100.0 / (1.0 + gains / losses))

def calc_sma(closes, period):
    if len(closes) < period: return closes[-1] if closes else 0.0
    return sum(closes[-period:]) / period

def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1: return 0.0001
    trs = []
    for i in range(1, period + 1):
        tr = max(highs[-i] - lows[-i], abs(highs[-i] - closes[-i - 1]) if i < len(closes) else 0, abs(lows[-i] - closes[-i - 1]) if i < len(closes) else 0)
        trs.append(tr)
    return sum(trs) / len(trs)

def in_trading_window():
    now = datetime.now(timezone.utc)
    for w in CONFIG["risk"]["trading_hours"]["windows"]:
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        s = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        e = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if s <= now <= e: return True
    return False

def fetch_snapshot(bridge, sym, tf, count=100):
    r = bridge.get_rates(sym, tf, count)
    if r.get("status") != "ok": return {"symbol": sym, "error": r.get("message", "unknown")}
    parts = r.get("raw", "").split("|", 4)
    if len(parts) < 4: return {"symbol": sym, "error": "malformed"}
    rates_str = parts[3].split("rates=")[-1] if "rates=" in parts[3] else ""
    candles = []
    for c in rates_str.split(";"):
        v = c.split(",")
        if len(v) >= 6: candles.append({"time": int(v[0]), "open": float(v[1]), "high": float(v[2]), "low": float(v[3]), "close": float(v[4]), "volume": int(v[5])})
    if not candles: return {"symbol": sym, "error": "no_candles"}
    closes = [c["close"] for c in candles]; highs = [c["high"] for c in candles]; lows = [c["low"] for c in candles]
    ind = {"rsi": round(calc_rsi(closes), 2), "sma_fast": round(calc_sma(closes, 9), 5), "sma_slow": round(calc_sma(closes, 21), 5), "atr": round(calc_atr(highs, lows, closes), 5)}
    signal = None
    if len(closes) >= 21:
        pf, ps, cf, cs = calc_sma(closes[:-1], 9), calc_sma(closes[:-1], 21), ind["sma_fast"], ind["sma_slow"]
        if pf <= ps and cf > cs: signal = "sma_crossover_long"
        if pf >= ps and cf < cs: signal = "sma_crossover_short"
        if signal and ind["rsi"] > 60 and "long" in signal: signal = None
        if signal and ind["rsi"] < 40 and "short" in signal: signal = None
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "symbol": sym, "timeframe": tf, "close": candles[-1]["close"], "indicators": ind, "signal_flag": signal}

def main():
    if CONFIG.get("state", "ACTIVE") != "ACTIVE":
        logger.info("State not ACTIVE; idle."); return
    if not in_trading_window():
        logger.info("Outside trading hours; idle."); return
    bridge = MT5IPCBridge(WORKSPACE)
    health = bridge.health()
    if not health.get("connected"):
        logger.warning("Bridge down: %s", health)
        with open(OUTBOX, "a") as f: f.write(f"\n## Scan | {datetime.now(timezone.utc).isoformat()} | BRIDGE_DOWN\n")
        return
    symbols = CONFIG["scanner"]["symbols"]
    tfs = CONFIG["scanner"]["timeframes"]
    signals = []
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        for tf in tfs:
            s = fetch_snapshot(bridge, sym, tf)
            (SNAP_DIR / f"{sym}_M{tf}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json").write_text(json.dumps(s, indent=2))
            if s.get("signal_flag"): signals.append(s); logger.info("SIGNAL: %s %s M%d", sym, s["signal_flag"], tf)
    with open(OUTBOX, "a") as f:
        f.write(f"\n## Scan | {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"**Scanned**: {len(symbols)*len(tfs)} | **Signals**: {len(signals)}\n")
    if signals:
        with open(SIGNAL_INBOX, "a") as f:
            for s in signals:
                f.write(f"\n## Task: signal-forge | {s['timestamp']}\n")
                f.write(f"### Signal: {s['symbol']} M{s['timeframe']} {s['signal_flag']}\n")
                f.write(f"### Indicators: {json.dumps(s['indicators'])}\n---\n")
    logger.info("Done. %d snapshots, %d signals.", len(symbols)*len(tfs), len(signals))

if __name__ == "__main__":
    main()
