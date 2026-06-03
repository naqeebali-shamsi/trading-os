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


import tempfile

def make_uptrend_then_breakout(symbol="SPY", n=50, base_price=400.0, breakout_volume=99999999, db_path=None):
    """Return a BarStore with a clean uptrend + volume breakout on the last bar."""
    store = BarStore([symbol], maxlen=100, db_path=db_path)
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
    with tempfile.TemporaryDirectory() as tmp:
        store = make_uptrend_then_breakout(db_path=os.path.join(tmp, "bars.db"))
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

    with tempfile.TemporaryDirectory() as tmp:
        store = make_uptrend_then_breakout(db_path=os.path.join(tmp, "bars.db"))
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

        j = TradeJournal(db_path=os.path.join(tmp, "journal.db"))
        j.log_signal(sig)
        j.log_order(tr)
        assert j.today_signals_count() == 1


def test_portfolio_heat():
    from autonome.risk.portfolio_heat import PortfolioHeat

    ph = PortfolioHeat(max_heat_pct=5.0, max_heat_per_sector_pct=3.0)
    equity = 100000.0

    # Register a position
    ph.register_position("AAPL", entry_price=200.0, stop_loss=190.0, qty=50.0, sector="TECH", conviction=0.8)
    # Heat = 50 * 200 * (10/200) = 10000 * 0.05 = $500
    assert ph.total_heat(equity) == 500.0 / equity

    # Register another in same sector
    ph.register_position("NVDA", entry_price=800.0, stop_loss=760.0, qty=20.0, sector="TECH", conviction=0.7)
    # Heat = 20 * 800 * (40/800) = 16000 * 0.05 = $800
    # Total heat = (500 + 800) / 100000 = 1.3%
    assert ph.total_heat(equity) == 1300.0 / equity

    # Proposed new position: $1500 heat (1.5% of equity) -> total = 2.8%, sector = 2.8% < 3%
    allowed, reason = ph.can_add_position(1500.0, equity, sector="TECH")
    assert allowed

    # Proposed new position: $2000 heat -> total = 3.3%, sector = 3.3% > 3%
    allowed2, reason2 = ph.can_add_position(2000.0, equity, sector="TECH")
    assert not allowed2
    assert "sector_heat_limit" in reason2

    # Add a 3rd position
    ph.register_position("AMD", entry_price=150.0, stop_loss=130.0, qty=100.0, sector="TECH", conviction=0.6)
    # Heat = 100 * 150 * (20/150) = 15000 * 0.133 = $2000
    # Total = 1300 + 2000 = 3300 => 3.3%

    # Proposed 4th TECH position: $2500 heat -> total = 5800 => 5.8% > 5%
    allowed, reason = ph.can_add_position(2500.0, equity, sector="TECH")
    assert not allowed
    assert "total_heat_limit" in reason

    # Different sector
    allowed2, _ = ph.can_add_position(1000.0, equity, sector="FINANCE")
    assert allowed2  # 3.3% + 1.0% = 4.3% < 5%

    # Conviction weight
    ph2 = PortfolioHeat()
    ph2.register_position("X", 100.0, 95.0, 10.0, conviction=0.4)
    ph2.register_position("Y", 100.0, 95.0, 10.0, conviction=0.6)
    # avg = 0.5, X at 0.4 -> ratio = 0.8
    scaled = ph2.conviction_weight("X", 100.0)
    assert 75.0 < scaled < 85.0

    print("PASS: test_portfolio_heat")


if __name__ == "__main__":
    test_momentum_detects_breakout()
    print("PASS: test_momentum_detects_breakout")
    test_risk_approves_and_rejects()
    print("PASS: test_risk_approves_and_rejects")
    test_full_pipeline_mock()
    print("PASS: test_full_pipeline_mock")
    test_portfolio_heat()
    print("\nALL TESTS PASSED")
