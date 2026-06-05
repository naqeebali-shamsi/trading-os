# NOMADCREW TRADING OS — SYSTEM BRIEF (for review swarm)

## Executive Summary
Multi-market autonomous trading and advisory system for US/Canada and India equity markets. Currently v4.0. Runs on Windows host via WSL2 Ubuntu 26.04.

## Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│                     SUPERVISOR (autonome/supervisor)            │
│  - Market hours orchestrator   - Paper trading mode              │
│  - Health monitoring           - Symbol rotation                │
└──────┬──────────────────────────┬───────────────────────────────┘
       │                        │
  ┌────▼────────────┐    ┌──────▼──────────┐
  │  US MARKET      │    │  INDIA MARKET   │
  │  (alpaca + yf)  │    │  (yfinance only)│
  └────┬────────┬───┘    └─────┬───────────┘
       │        │              │
  ┌────▼──┐  ┌─▼────┐   ┌─────▼────────┐
  │Trading│  │Dark  │   │Long-Term Gems│
  │Strat  │  │Horse │   │(Quality+Value)│
  └───────┘  └──────┘   └──────────────┘
```

## Components

### US Trading (Paper, not live)
- **Data**: `data/bars.py`, `data/yahoo_feed.py`, `data/vix_feed.py`
- **Strategies**: `strategy/ema_crossover.py`, `strategy/momentum_breakout.py`, `strategy/pullback_to_ema.py`, `strategy/regime.py`, `strategy/router.py`
- **Execution**: `execution/engine.py`, `execution/limit_entry.py`, `execution/reconcile.py`
- **Discovery**: `discovery/dark_horse.py`, `discovery/sector_rotation.py`, `discovery/news_sentinel.py`
- **Broker**: `broker/alpaca_client.py` (paper keys)
- **TimesFM**: `intelligence/timesfm_adapter_production.py`, `intelligence/timesfm_real.py`

### India Advisory (Manual execution)
- **Signals**: `india/signals.py`, `india/fundamentals.py`, `india/strategy.py`
- **Macro**: `india/sentinel.py` (USD/INR, oil, gold)
- **Risk**: `india/risk.py`
- **Dashboard**: `dashboard/india.html` served by `scripts/india_dashboard_server.py:8766`
- **Runner**: `scripts/run_india_discovery.py`

### Long-Term Value Discovery (Both markets)
- **US/Canada**: `longterm/us_screener.py` — Quality + Value + Growth scoring
- **India**: `longterm/india_screener.py` — Graham-Buffett criteria
- **Discovery**: `scripts/longterm_discovery.py`
- **Dashboard**: `dashboard/longterm.html`

### Risk & Portfolio
- `risk/portfolio_heat.py`, `risk/risk_manager.py`
- `journal/trade_journal.py`

### Intelligence Layer
- `intelligence/llm_gate.py`, `intelligence/dreampod.py`, `intelligence/regime_forecaster.py`
- `intelligence/discovery.py` — multi-source signal aggregator

## Cron Jobs
| Job | Schedule | File |
|-----|----------|------|
| dark-horse-discovery | Mon-Fri 8 AM ET | `discovery/dark_horse.py` |
| india-discovery | Mondays 2 AM UTC | `scripts/run_india_discovery.py` |
| longterm-discovery | Mondays 3 AM UTC | `scripts/longterm_discovery.py` |

## Data Artifacts
- `swarm/intel/india_signals.json` — India daily signals
- `swarm/intel/india_macro.json` — India macro regime
- `swarm/intel/longterm_gems.json` — Long-term gems US + India

## Key Concerns (user stated)
1. "Currently lower than mediocre and not making money"
2. "Trying too hard to be deterministic and very surface-level"
3. "Missing opportunities or fundamentally broken"
4. "I need a fully autonomous system that can discover and trade stocks and make real profits"
5. User executes India trades manually to save broker API fees
6. Dashboard runs at localhost:8765 (US) and localhost:8766 (India)

## File Paths (review from these)
- Code: `/mnt/e/NomadCrew[GROWTH]/trading-os/v2/`
- Dashboards: `/mnt/e/NomadCrew[GROWTH]/trading-os/v2/dashboard/`
- Intel: `/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/`
