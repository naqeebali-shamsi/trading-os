"""
v2/tests/test_pipeline.py
End-to-end test with mocked Alpaca client.
Run: cd /mnt/e/NomadCrew[GROWTH]/trading-os/v2 && python3 tests/test_pipeline.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import statistics
from datetime import datetime, timedelta, timezone

from autonome.data.bars import Bar, BarStore
from autonome.strategy.momentum_breakout import MomentumBreakout
from autonome.risk.risk_manager import RiskManager

random.seed(42)


class FakeAccount:
    equity = 100000.0
    buying_power = 100000.0
    cash = 100000.0
    daytrade_count = 0
    status = "ACTIVE"


class FakeAlpaca:
    def fetch_account(self):
        return FakeAccount()
    def list_positions(self):
        return []
    def is_market_open(self):
        return True
    def submit_order(self, **kw):
        class R:
            pass
        r = R()
        r.id = "order_" + str(random.randint(1000, 9999))
        r.symbol = kw.get("symbol")
        r.side = kw.get("side")
        r.qty = kw.get("qty")
        r.filled_avg_price = kw.get("stop_price", 450.0) or 450.0
        r.filled_qty = r.qty
        r.status = "filled"
        r.error = None
        r.raw = {}
        return r
    def get_order(self, oid):
        return None
    def cancel_all_orders(self):
        pass
    def list_orders(self, status="open", limit=500):
        return []


def make_uptrend_then_breakout(symbol="SPY", n=50, base_price=400.0, breakout_volume=99999999):
    """Return a BarStore with a clean uptrend + volume breakout on the last bar."""
    store = BarStore([symbol], maxlen=100)
    price = base_price
    for i in range(n):
        t = datetime.now(timezone.utc) - timedelta(hours=n - i)
        o = price + i * 1.2
        c = o + 0.5
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        v = 1000000 + i * 10000
        store.ingest(Bar(symbol, t, o, h, l, c, v))
        price = c

    prev = store.last(symbol)
    last_t = datetime.now(timezone.utc)
    breakout = Bar(
        symbol, last_t,
        open=prev.high - 0.2,
        high=prev.high + 2.5,
        low=prev.low,
        close=prev.high + 2.0,
        volume=breakout_volume,
    )
    store.ingest(breakout)
    return store


def test_momentum_detects_breakout():
    store = make_uptrend_then_breakout()
    strat = MomentumBreakout({
        "ema_fast": 9, "ema_slow": 21, "volume_surge_z": 1.0,
        "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 3.0,
        "cooldown_bars": 0
    })
    sig = strat.scan("SPY", store, global_bar_idx=999)
    assert sig is not None
    assert sig.direction == "LONG"
    assert sig.stop_loss < sig.entry_price < sig.take_profit


def test_risk_approves_and_rejects():
    rm = RiskManager()
    acc = FakeAccount()

    # Normal
    rd = rm.evaluate(acc, [], 0.8, 500.0, 490.0, 530.0, "SPY", "LONG")
    assert rd.approved and rd.qty > 0

    # Bad stop/target for long
    rd2 = rm.evaluate(acc, [], 0.8, 500.0, 510.0, 490.0, "SPY", "LONG")
    assert not rd2.approved

    # Zero confidence
    rd3 = rm.evaluate(acc, [], 0.0, 500.0, 490.0, 530.0, "QQQ", "LONG")
    assert not rd3.approved

    # Max positions
    class FP:
        symbol = "X"
        qty = 1.0
        avg_entry_price = 1.0
        current_price = 1.0
        unrealized_pl = 0.0
        unrealized_plpc = 0.0
    rd4 = rm.evaluate(acc, [FP() for _ in range(10)], 0.8, 500.0, 490.0, 530.0, "TSLA", "LONG")
    assert not rd4.approved


def test_full_pipeline_mock():
    from autonome.execution.engine import ExecutionEngine, TradeRecord
    from autonome.journal.trade_journal import TradeJournal
    import tempfile

    store = make_uptrend_then_breakout()
    strat = MomentumBreakout({
        "ema_fast": 9, "ema_slow": 21, "volume_surge_z": 1.0,
        "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 3.0,
        "cooldown_bars": 0
    })
    sig = strat.scan("SPY", store, global_bar_idx=100)
    assert sig is not None

    rm = RiskManager()
    rd = rm.evaluate(
        FakeAccount(), [], sig.confidence, sig.entry_price,
        sig.stop_loss, sig.take_profit, "SPY", sig.direction,
    )
    assert rd.approved

    engine = ExecutionEngine(FakeAlpaca())
    tr = engine.enter_position(sig, rd)
    assert tr.status == "OPEN"

    with tempfile.TemporaryDirectory() as tmp:
        j = TradeJournal(db_path=os.path.join(tmp, "t.db"))
        j.log_signal(sig)
        j.log_order(tr)
        assert j.today_signals_count() == 1


if __name__ == "__main__":
    test_momentum_detects_breakout()
    print("PASS: test_momentum_detects_breakout")
    test_risk_approves_and_rejects()
    print("PASS: test_risk_approves_and_rejects")
    test_full_pipeline_mock()
    print("PASS: test_full_pipeline_mock")
    print("\nALL TESTS PASSED")
