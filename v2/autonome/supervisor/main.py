"""
autonome/supervisor/main.py  v2.0
24x7 supervisor loop: data -> signal -> risk -> execute -> journal.
Minimal. Deterministic. No LLM.
"""
from __future__ import annotations

import os, sys, time, json, logging, signal
from datetime import datetime, timedelta
from typing import Optional

import yaml

# ensure import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from autonome.broker.alpaca_client import AlpacaClient
from autonome.data.bars import BarStore, AlpacaDataFeed, Bar
from autonome.strategy.momentum_breakout import MomentumBreakout, Signal
from autonome.risk.risk_manager import RiskManager
from autonome.execution.engine import ExecutionEngine, TradeRecord
from autonome.journal.trade_journal import TradeJournal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("supervisor")

# ── config ─────────────────────────────────────────────────────────────
CFG_PATH = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")


def load_settings() -> dict:
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


# ── state ──────────────────────────────────────────────────────────────
class State:
    def __init__(self):
        self.cfg = load_settings()
        self.symbols: list[str] = self.cfg["data"]["symbols"]
        self.timeframe: str = self.cfg["data"]["timeframe"]
        self.heartbeat_sec: int = self.cfg["system"]["heartbeat_interval_sec"]
        self.mode: str = self.cfg["system"]["mode"]
        self.market_hours_only: bool = self.cfg["system"]["market_hours_only"]

        self.client = AlpacaClient(mode=self.mode)
        self.store = BarStore(self.symbols, maxlen=500)
        self.feed = AlpacaDataFeed()
        self.strategy = MomentumBreakout(self.cfg["strategy"]["params"])
        self.risk = RiskManager()
        self.execution = ExecutionEngine(self.client)
        self.journal = TradeJournal()

        self.global_bar_idx = 0
        self.last_equity_log = datetime.min
        self.last_daily_reset = datetime.utcnow().date()
        self._running = True

    def shutdown(self, *_):
        log.warning("Shutdown signal received")
        self._running = False


# ── helpers ────────────────────────────────────────────────────────────
def next_bar_wait_seconds(tf: str) -> float:
    """Rough estimate for 1Hour bars: wait until next top of hour + 5s."""
    now = datetime.utcnow()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
    return max(1.0, (next_hour - now).total_seconds())


def write_state_json(st: State):
    """Live health dump for external dashboards."""
    path = os.path.join(os.path.dirname(__file__), "../../data/state.json")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "t": datetime.utcnow().isoformat(),
                "mode": st.mode,
                "running": st._running,
                "bar_idx": st.global_bar_idx,
                "symbols_warm": {s: len(st.store.history(s, 999)) for s in st.symbols},
                "halted": st.risk.halted,
            }, f)
    except Exception:
        log.exception("state write failed")


# ── main loop ──────────────────────────────────────────────────────────
def loop(st: State):
    log.info("=== AUTONOME v2.0 supervisor === mode=%s", st.mode)

    # initial warm
    log.info("Warming bar store...")
    st.feed.warm_store(st.store)
    log.info("Warm complete")

    # first account snapshot
    try:
        acc = st.client.fetch_account()
        st.risk.reset_day(acc.equity)
        st.journal.log_equity(acc.equity, acc.buying_power, acc.cash, 0.0, 0)
        log.info("Equity=%.2f BP=%.2f", acc.equity, acc.buying_power)
    except Exception:
        log.exception("Initial account fetch failed -- aborting")
        return

    while st._running:
        write_state_json(st)

        # daily reset
        today = datetime.utcnow().date()
        if today != st.last_daily_reset:
            st.last_daily_reset = today
            try:
                acc = st.client.fetch_account()
                st.risk.reset_day(acc.equity)
                log.info("New day reset. Equity=%.2f", acc.equity)
            except Exception:
                log.exception("Daily reset failed")

        # market hours gate
        if st.market_hours_only:
            try:
                if not st.client.is_market_open():
                    sleep = 60
                    log.debug("Market closed -- sleep %ds", sleep)
                    time.sleep(sleep)
                    continue
            except Exception:
                log.exception("Clock check failed")
                time.sleep(30)
                continue

        # ── fetch new bars ─────────────────────────────────────────────
        for sym in st.symbols:
            try:
                bars = st.feed.fetch_history(sym, limit=5)
                for b in bars:
                    # simple dedup: if bar t > last stored t, ingest
                    last = st.store.last(sym)
                    if last is None or b.t > last.t:
                        st.store.ingest(b)
                        st.global_bar_idx += 1

                        # ── strategy ───────────────────────────────
                        sig = st.strategy.scan(sym, st.store, st.global_bar_idx)
                        if sig:
                            log.info("SIGNAL %s %s @ %.2f (conf=%.2f)",
                                     sig.symbol, sig.direction, sig.entry_price, sig.confidence)
                            st.journal.log_signal(sig)

                            # ── risk ───────────────────────────────
                            acc = st.client.get_account()
                            positions = st.client.list_positions()
                            rd = st.risk.evaluate(
                                account=acc,
                                positions=positions,
                                signal_confidence=sig.confidence,
                                entry_price=sig.entry_price,
                                stop_loss=sig.stop_loss,
                                target_price=sig.take_profit,
                                symbol=sig.symbol,
                                direction=sig.direction,
                            )
                            if not rd.approved:
                                log.warning("RISK REJECTED %s: %s", sig.symbol, rd.reason)
                                continue
                            log.info("RISK APPROVED %s qty=%.0f", sig.symbol, rd.qty)

                            # ── execute ────────────────────────────
                            tr = st.execution.enter_position(sig, rd)
                            st.journal.log_order(tr)
                            if tr.status == "OPEN":
                                log.info("ENTER %s %s qty=%.0f @ %.2f",
                                         tr.symbol, tr.side, tr.qty, tr.entry_price or 0)
                            else:
                                log.error("ENTER FAILED %s: %s", tr.symbol, tr.error)
            except Exception:
                log.exception("Bar cycle failed for %s", sym)

        # ── periodic tasks ─────────────────────────────────────────────
        now = datetime.utcnow()
        if (now - st.last_equity_log).total_seconds() >= 300:
            try:
                acc = st.client.fetch_account()
                positions = st.client.list_positions()
                dd = st.risk.current_drawdown(acc.equity)
                st.journal.log_equity(acc.equity, acc.buying_power, acc.cash, dd, len(positions))
                log.info("Equity=%.2f DD=%.2%% Pos=%d", acc.equity, dd * 100, len(positions))
                st.last_equity_log = now
            except Exception:
                log.exception("Equity snapshot failed")

        # heartbeat / sleep
        sleep_sec = st.heartbeat_sec
        if st.timeframe == "1Hour":
            sleep_sec = min(300, next_bar_wait_seconds(st.timeframe))
        log.debug("Sleep %.0fs", sleep_sec)
        time.sleep(sleep_sec)

    log.info("Supervisor loop exited cleanly")


# ── entry ──────────────────────────────────────────────────────────────
def main():
    st = State()
    signal.signal(signal.SIGTERM, st.shutdown)
    signal.signal(signal.SIGINT, st.shutdown)
    try:
        loop(st)
    finally:
        write_state_json(st)


if __name__ == "__main__":
    main()
