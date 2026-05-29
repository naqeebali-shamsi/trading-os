# Trading OS Autonomy Roadmap

Phases A–D implemented in code; use this doc as the operator checklist.

## Phase A — Safe boot

- `kernel/preflight.py` runs before supervisor layers in LIVE mode
- `ops/readiness_eval.py` shared with `scripts/readiness_gate.py`
- Tests: `tests/test_supervisor_preflight.py`, `tests/test_autonomy_instrumentation.py`

## Phase B — Coherent decisions

- `cortex/macro_risk_policy.py` publishes `risk.macro_policy` from event radar
- `cortex/main.py` loads open positions from `pnl_sync`
- `immune/main.py` applies macro policy + pnl_sync position counts
- `signal_generator_v2` consumes macro policy for direct intents

## Phase C — Operations

- `muscle/pnl_sync_daemon.py` in supervisor boot
- Supervisor publishes `ops.layer.restarted` on child restart
- Dashboard: `GET /api/preflight`, `GET /api/health/alerts`, `GET /api/agent/context`
- `/api/state` uses `_preflight_cached()` and portfolio panel timeouts (1.2s) with last-good cache so slow broker readiness never blocks polling
- Trader Desk **Forecast Thesis** panel (`market.forecast*`) shows staleness, macro conflict, and recent direction history
- Trader Desk **Edge Validation** panel reads `intel/edge_gate_report.json` from the edge ledger daemon
- Trade lifecycle panel joins `introspect.post_trade_review`, surfaces immune block reasons, and flags missing joins as defects
- `nervous/bus.py` `tail(n)` reads backward from EOF for unfiltered tails (large bus files)
- MCP: `bridge/context_mcp_server.py` (read-only), `bridge/mt5_mcp_server.py` (hook-gated trade tools)

## Phase D — Bounded learning

- `TRADING_OS_PROFILE=production` (default): direct intents, stock intents, and learner auto-apply on unless explicitly overridden
- `TRADING_OS_LEARNER_AUTO_APPLY` still wins when set; use `TRADING_OS_PROFILE=development` or `observe` for safe local bootstrap
- Post-trade path remains advisory-only when brain mode is ADVISORY

## Phase E — Dream Lab (human-gated R&D)

- `rd/dream_scheduler.py` supervised as `rd.dream` — hourly / 6h / daily cycles + triggered R&D
- Agents propose improvements to `intel/promotion_queue.jsonl`; **never** mutate live trading directly
- Approve via `scripts/approve_promotion.py` or Trader Desk **Pending Improvements** panel (`POST /api/promotions/approve`)
- Approved patches materialize in `intel/live_policy.json`; read by `strategy_registry`, `signal_generator_v2`, `signal_research`, `macro_lexicon`
- `memory/learner.py` proposes promotions when `config/dream_lab.yaml` has `human_approval_required: true`
- Session-closed symbols defer at preflight (bridge up, market closed) so LIVE stack + Dream Lab can run on weekends
- Trader Desk **Portfolio P&L** panel reads broker `ACCOUNT|` + open legs from `data_out.txt` via `muscle/portfolio_snapshot.py`

## Operator commands

```bash
python3 scripts/readiness_gate.py --live --strict-instruments
TRADING_OS_MODE=LIVE python3 kernel/supervisor.py
python3 scripts/approve_promotion.py list
python3 scripts/approve_promotion.py approve promo_YYYYMMDD_xxxxxxxx
python3 rd/dream_scheduler.py --once hourly
python3 scripts/enable_stock_trading.py --preset demo_stocks
```
