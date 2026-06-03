"""
autonome/supervisor/main.py  v2.3
24x7 supervisor loop: data -> signal -> LLM gate -> risk -> execute -> journal.
Includes earnings avoidance, broker reconciliation, journal rotation, LIVE guard.
"""
from __future__ import annotations

import os, sys, time, json, logging, signal
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from autonome.broker.alpaca_client import AlpacaClient
from autonome.data.bars import BarStore, AlpacaDataFeed
from autonome.data.vix_feed import fetch_vix
from autonome.data.earnings import EarningsCalendar
from autonome.strategy.router import StrategyRouter
from autonome.risk.risk_manager import RiskManager
from autonome.execution.engine import ExecutionEngine
from autonome.execution.reconcile import Reconciler
from autonome.journal.trade_journal import TradeJournal
from autonome.intelligence.llm_gate import LLMGate, SignalContext
from autonome.intelligence.timesfm_adapter_production import TimesFMAdapter
from autonome.alerts.telegram import TelegramAlertSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("supervisor")

# ── config ─────────────────────────────────────────────────────────────
CFG_PATH = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
SEC_PATH = os.path.join(os.path.dirname(__file__), "../../config/secrets.yaml")


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
        self.strategy = StrategyRouter(
            {
                "momentum": self.cfg["strategy"]["params"],
                "pullback": self.cfg.get("strategy", {}).get("pullback", {}),
                "crossover": self.cfg.get("strategy", {}).get("crossover", {}),
            },
            use_llm=False,
        )
        self.risk = RiskManager()
        self.execution = ExecutionEngine(self.client)
        self.journal = TradeJournal()
        self.llm_gate = LLMGate()
        self.alerts = TelegramAlertSender()
        self.reconciler = Reconciler()

        # earnings calendar
        self.earnings: Optional[EarningsCalendar] = None
        if self.cfg.get("data", {}).get("earnings_enabled", False):
            try:
                with open(SEC_PATH) as f:
                    secs = yaml.safe_load(f)
                finnhub_key = secs.get("finnhub", {}).get("api_key", "")
                if finnhub_key:
                    self.earnings = EarningsCalendar(api_key=finnhub_key)
                    self.strategy.earnings_calendar = self.earnings
                    self.strategy.earnings_enabled = True
                    self.strategy.earnings_buffer_days = self.cfg["data"].get("earnings_buffer_days", 2)
            except Exception:
                log.warning("Failed to init earnings calendar")

        self.global_bar_idx = 0
        self.last_equity_log = datetime.min.replace(tzinfo=timezone.utc)
        self.last_daily_reset = datetime.now(timezone.utc).date()
        self.last_order_sync = datetime.min.replace(tzinfo=timezone.utc)
        self.last_reconcile = datetime.min.replace(tzinfo=timezone.utc)
        self.consecutive_api_failures = 0
        self._running = True
        self.prev_positions: dict[str, object] = {}
        self.last_vix: Optional[float] = None
        self.last_vix_fetch: Optional[datetime] = None
        self.consecutive_stale_cycles = 0

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
                "vix": st.last_vix,
                "vix_fetch": st.last_vix_fetch.isoformat() if st.last_vix_fetch else None,
            }, f)
    except Exception:
        log.exception("state write failed")


# ── API health ─────────────────────────────────────────────────────────
MAX_CONSECUTIVE_API_FAILURES = 5
API_FAILURE_HALT_TIMEOUT_SEC = 60


def _handle_api_failure(st: State, context: str) -> bool:
    st.consecutive_api_failures += 1
    log.error("API failure [%s] #%d/%d", context, st.consecutive_api_failures, MAX_CONSECUTIVE_API_FAILURES)
    if st.consecutive_api_failures >= MAX_CONSECUTIVE_API_FAILURES:
        st.risk.halted = True
        st.risk._save_halt_state()
        st.alerts.send_api_halt(st.consecutive_api_failures, context)
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
    log.info("=== AUTONOME v2.3 supervisor === mode=%s", st.mode)

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

        # LIVE mode periodic warning
        if st.mode == "LIVE":
            log.critical("LIVE TRADING MODE — REAL MONEY AT RISK")

        # Halt check
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
                # warm earnings cache for all symbols
                if st.earnings:
                    for sym in st.symbols:
                        try:
                            st.earnings.fetch_earnings(sym)
                        except Exception:
                            pass
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

        # ── broker reconciliation (hourly) ──────────────────────────────────
        if (now - st.last_reconcile).total_seconds() >= 3600:
            try:
                pos_disc = st.reconciler.reconcile_positions(st.client, st.journal)
                ord_disc = st.reconciler.reconcile_orders(st.client)
                total = len(pos_disc) + len(ord_disc)
                if total > 0:
                    log.warning("RECONCILE FOUND %d discrepancies", total)
                    st.alerts.send_alert("Reconciliation", f"{total} discrepancies found")
                else:
                    log.info("RECONCILE OK")
                st.last_reconcile = now
            except Exception:
                log.warning("Reconciliation failed (non-fatal)")

        # ── journal rotation check ──────────────────────────────────────────
        try:
            if st.journal.db_size_mb() > 500:
                log.warning("Journal DB >500MB — auto-rotating")
                st.journal.rotate(keep_months=1)
        except Exception:
            pass

        # ── systematic data staleness check ────────────────────────────────
        max_staleness_sec = 3600 * 2
        any_stale, stale_symbols = st.store.any_stale(max_staleness_sec)
        if any_stale:
            st.consecutive_stale_cycles += 1
            log.warning("STALE DATA %s (consecutive=%d/3)", stale_symbols, st.consecutive_stale_cycles)
            if st.consecutive_stale_cycles >= 3:
                log.error("SOFT HALT: data stale for 3+ cycles — pausing until fresh")
                time.sleep(300)
                continue
        else:
            if st.consecutive_stale_cycles > 0:
                log.info("Data fresh again — clearing stale counter")
            st.consecutive_stale_cycles = 0

        # ── fetch new bars ─────────────────────────────────────────────────
        for sym in st.symbols:
            try:
                bars = st.feed.fetch_history(sym, limit=5)
                for b in bars:
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

                            hist = st.store.history(sym, 30)
                            closes = [bar.close for bar in hist] if hist else []

                            vix_data = fetch_vix()
                            vix_val = None
                            if vix_data:
                                vix_val, _ = vix_data
                                st.last_vix = vix_val
                                st.last_vix_fetch = datetime.now(timezone.utc)

                            # ── TimesFM forecast filter ───────────────────────
                            hist = st.store.history(sym, 50)
                            if hist and len(hist) >= 20:
                                fc = st.forecaster.forecast(sym, hist, horizon=5)
                                if not st.forecaster.should_trade(fc, sig.direction):
                                    log.warning("TIMESFM BLOCKED %s — forecast contradicts %s (regime=%s)",
                                                sym, sig.direction, fc.get("regime", "unknown"))
                                    continue

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
                                vix=vix_val,
                            )
                            if not rd.approved:
                                log.warning("RISK REJECTED %s: %s", sig.symbol, rd.reason)
                                if rd.reason.startswith("drawdown_limit"):
                                    st.alerts.send_drawdown_halt(st.risk.current_drawdown(acc.equity), acc.equity)
                                elif rd.reason == "daily_loss_limit":
                                    st.alerts.send_daily_loss_halt(st.risk.daily_loss_accum, acc.equity)
                                elif rd.reason.startswith("volatility_halt"):
                                    vol = float(rd.reason.split("_")[-1].rstrip("%")) / 100.0 if "_" in rd.reason else 0.0
                                    st.alerts.send_volatility_halt(vol)
                                continue
                            log.info("RISK APPROVED %s qty=%.4f", sig.symbol, rd.qty)

                            # ── execute ────────────────────────────
                            tr = st.execution.enter_position(sig, rd)
                            st.journal.log_order(tr)
                            if tr.status == "OPEN":
                                # Register heat ONLY after confirmed fill
                                st.risk.commit_trade(
                                    tr.symbol, tr.entry_price or sig.entry_price,
                                    sig.stop_loss, tr.qty, None, sig.confidence
                                )
                                log.info("ENTER %s %s qty=%.4f @ %.2f",
                                         tr.symbol, tr.side, tr.qty, tr.entry_price or 0)
                                st.alerts.send_position_entered(tr)
                            else:
                                # Undo any pre-registered heat
                                st.risk.unregister_trade(tr.symbol)
                                log.error("ENTER FAILED %s: %s", tr.symbol, tr.error)
                                st.alerts.send_order_rejected(tr, tr.error)
            except Exception:
                log.exception("Bar cycle failed for %s", sym)

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

                prev_syms = set(st.prev_positions.keys())
                curr_syms = {p.symbol for p in positions}
                exited = prev_syms - curr_syms
                for sym in exited:
                    pp = st.prev_positions[sym]
                    side = pp.direction if hasattr(pp, "direction") else ("LONG" if float(pp.qty) > 0 else "SHORT")
                    entry_price = float(pp.avg_entry_price)
                    if hasattr(pp, "current_price"):
                        pnl = (pp.current_price - entry_price) * pp.qty if side == "LONG" else (entry_price - pp.current_price) * abs(pp.qty)
                    else:
                        pnl = None
                    st.alerts.send_position_exited(pp, pnl)
                st.prev_positions = {p.symbol: p for p in positions}
            except Exception:
                if _handle_api_failure(st, "equity_snapshot"):
                    continue

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
