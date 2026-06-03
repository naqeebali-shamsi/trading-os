"""
autonome/supervisor/main.py  v2.2
24x7 supervisor loop: data -> signal -> LLM gate -> risk -> execute -> journal.
Includes API failure hard stop, data staleness guard, order lifecycle sync.
"""
from __future__ import annotations

import os, sys, time, json, logging, signal
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from autonome.broker.alpaca_client import AlpacaClient
from autonome.data.bars import BarStore, AlpacaDataFeed
from autonome.strategy.momentum_breakout import MomentumBreakout
from autonome.risk.risk_manager import RiskManager
from autonome.execution.engine import ExecutionEngine
from autonome.journal.trade_journal import TradeJournal
from autonome.intelligence.llm_gate import LLMGate, SignalContext

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
        self.llm_gate = LLMGate()

        self.global_bar_idx = 0
        self.last_equity_log = datetime.min.replace(tzinfo=timezone.utc)
        self.last_daily_reset = datetime.now(timezone.utc).date()
        self.last_order_sync = datetime.min.replace(tzinfo=timezone.utc)
        self.consecutive_api_failures = 0
        self._running = True

    def shutdown(self, *_):
        log.warning("Shutdown signal received")
        self._running = False


# ── helpers ────────────────────────────────────────────────────────────
def next_bar_wait_seconds(tf: str) -> float:
    """Rough estimate for 1Hour bars: wait until next top of hour + 5s."""
    now = datetime.now(timezone.utc)
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
    return max(1.0, (next_hour - now).total_seconds())


def write_state_json(st: State):
    path = os.path.join(os.path.dirname(__file__), "../../data/state.json")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "t": datetime.now(timezone.utc).isoformat(),
                "mode": st.mode,
                "running": st._running,
                "bar_idx": st.global_bar_idx,
                "symbols_warm": {s: len(st.store.history(s, 999)) for s in st.symbols},
                "halted": st.risk.halted,
            }, f)
    except Exception:
        log.exception("state write failed")


# ── API health ─────────────────────────────────────────────────────────
MAX_CONSECUTIVE_API_FAILURES = 5
API_FAILURE_HALT_TIMEOUT_SEC = 60  # pause after halt trigger


def _handle_api_failure(st: State, context: str) -> bool:
    """Track consecutive failures. Return True if system should HALT."""
    st.consecutive_api_failures += 1
    log.error("API failure [%s] #%d/%d", context, st.consecutive_api_failures, MAX_CONSECUTIVE_API_FAILURES)
    if st.consecutive_api_failures >= MAX_CONSECUTIVE_API_FAILURES:
        st.risk.halted = True
        st.risk._save_halt_state()
        log.critical("HALT TRIGGERED: %d consecutive API failures. Manual resume required.",
                     st.consecutive_api_failures)
        return True
    return False


def _reset_api_failures(st: State):
    if st.consecutive_api_failures > 0:
        log.info("API failures cleared (was %d)", st.consecutive_api_failures)
    st.consecutive_api_failures = 0


# ── main loop ──────────────────────────────────────────────────────────
def loop(st: State):
    log.info("=== AUTONOME v2.2 supervisor === mode=%s", st.mode)

    # initial warm
    log.info("Warming bar store...")
    st.feed.warm_store(st.store)
    log.info("Warm complete")

    # first account snapshot
    try:
        acc = st.client.fetch_account()
        st.risk.reset_day(acc.equity)
        st.journal.log_equity(acc.equity, acc.buying_power, acc.cash, 0.0, 0)
        _reset_api_failures(st)
        log.info("Equity=%.2f BP=%.2f", acc.equity, acc.buying_power)
    except Exception:
        log.exception("Initial account fetch failed -- aborting")
        return

    while st._running:
        write_state_json(st)

        # Halt check at top of loop
        if st.risk.halted:
            log.warning("System is HALTED — sleeping 60s...")
            time.sleep(API_FAILURE_HALT_TIMEOUT_SEC)
            continue

        # daily reset
        today = datetime.now(timezone.utc).date()
        if today != st.last_daily_reset:
            st.last_daily_reset = today
            try:
                acc = st.client.fetch_account()
                _reset_api_failures(st)
                st.risk.reset_day(acc.equity)
                log.info("New day reset. Equity=%.2f", acc.equity)
            except Exception:
                if _handle_api_failure(st, "daily_reset"):
                    continue
                time.sleep(30)
                continue

        # market hours gate
        if st.market_hours_only:
            try:
                if not st.client.is_market_open():
                    _reset_api_failures(st)
                    sleep = 60
                    log.debug("Market closed -- sleep %ds", sleep)
                    time.sleep(sleep)
                    continue
            except Exception:
                if _handle_api_failure(st, "market_clock"):
                    continue
                time.sleep(30)
                continue

        # ── order lifecycle sync (every 120s) ───────────────────────────────
        now = datetime.now(timezone.utc)
        if (now - st.last_order_sync).total_seconds() >= 120:
            try:
                changes = st.execution.sync_orders()
                if changes:
                    for c in changes:
                        log.info("ORDER SYNC: %s %s", c["id"], c["status"])
                st.last_order_sync = now
            except Exception:
                log.warning("Order sync failed (non-fatal)")

        # ── fetch new bars ─────────────────────────────────────────────────
        max_staleness_sec = 3600 * 2  # 2 hours
        any_stale = False

        for sym in st.symbols:
            try:
                bars = st.feed.fetch_history(sym, limit=5)
                for b in bars:
                    # staleness check
                    bar_age_sec = (datetime.now(timezone.utc) - b.t).total_seconds()
                    if bar_age_sec > max_staleness_sec:
                        log.warning("STALE BAR %s age=%.0fs — skipping", sym, bar_age_sec)
                        any_stale = True
                        continue

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

                            # ── LLM GATE ─────────────────────────────
                            gctx = SignalContext(
                                symbol=sig.symbol,
                                direction=sig.direction,
                                entry_price=sig.entry_price,
                                stop_loss=sig.stop_loss,
                                take_profit=sig.take_profit,
                                confidence=sig.confidence,
                                strategy="momentum_breakout",
                                regime="unknown",
                            )
                            gate = st.llm_gate.review(gctx)
                            log.info("LLM GATE %s: %s (conf=%.2f) reason=%s",
                                     sig.symbol, gate.decision, gate.confidence,
                                     gate.reasoning[:60])

                            if gate.decision == "REJECT":
                                log.warning("LLM REJECTED %s: %s", sig.symbol, gate.reasoning)
                                st.journal.log_signal(sig, meta={"gate": "REJECTED", "reason": gate.reasoning})
                                continue

                            if gate.decision == "MODIFY":
                                sig = st.llm_gate.apply_modifications(sig, gate)

                            # ── risk ─────────────────────────────────
                            acc = st.client.fetch_account()
                            positions = st.client.list_positions()
                            _reset_api_failures(st)

                            # get recent closes for vol calc
                            hist = st.store.history(sym, 30)
                            closes = [bar.close for bar in hist] if hist else []

                            rd = st.risk.evaluate(
                                account=acc,
                                positions=positions,
                                signal_confidence=sig.confidence,
                                entry_price=sig.entry_price,
                                stop_loss=sig.stop_loss,
                                target_price=sig.take_profit,
                                symbol=sig.symbol,
                                direction=sig.direction,
                                closes=closes,
                            )
                            if not rd.approved:
                                log.warning("RISK REJECTED %s: %s", sig.symbol, rd.reason)
                                continue
                            log.info("RISK APPROVED %s qty=%.4f", sig.symbol, rd.qty)

                            # ── execute ────────────────────────────
                            tr = st.execution.enter_position(sig, rd)
                            st.journal.log_order(tr)
                            if tr.status == "OPEN":
                                log.info("ENTER %s %s qty=%.4f @ %.2f",
                                         tr.symbol, tr.side, tr.qty, tr.entry_price or 0)
                            else:
                                log.error("ENTER FAILED %s: %s", tr.symbol, tr.error)
            except Exception:
                log.exception("Bar cycle failed for %s", sym)

        if any_stale:
            log.warning("Some bars were stale this cycle — consider checking data feed")

        # ── periodic tasks ─────────────────────────────────────────────────
        now = datetime.now(timezone.utc)
        if (now - st.last_equity_log).total_seconds() >= 300:
            try:
                acc = st.client.fetch_account()
                positions = st.client.list_positions()
                _reset_api_failures(st)
                dd = st.risk.current_drawdown(acc.equity)
                st.journal.log_equity(acc.equity, acc.buying_power, acc.cash, dd, len(positions))
                log.info("Equity=%.2f DD=%.2f%% Pos=%d", acc.equity, dd * 100, len(positions))
                st.last_equity_log = now
            except Exception:
                if _handle_api_failure(st, "equity_snapshot"):
                    continue

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
