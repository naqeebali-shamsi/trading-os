# Autonome Trading OS v2.3

AI-augmented autonomous trading system. Deterministic execution + LLM intelligence.

## Architecture

```
Data Feed → Strategy → LLM Gate → Risk Manager → Execution Engine → Journal
                ↑           ↑            ↑
           DreamPod    Playbook    Order Lifecycle
         (overnight)   (thesis)     (sync loop)

Discovery Engine (every 6h) → New stock candidates → Playbook updates
```

## Quick Start

```bash
cd /mnt/e/NomadCrew[GROWTH]/trading-os/v2

# 1. Configure secrets
nano config/secrets.yaml
#   alpaca.api_key: "PK..."
#   alpaca.api_secret: "..."
#   openrouter.api_key: "sk-or-v1-..."   # optional, for LLM Gate
#   newsapi.api_key: "..."               # optional, for Discovery
#   finnhub.api_key: "..."               # optional, for earnings calendar

# 2. Install systemd services
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable autonome-supervisor.service
systemctl --user enable autonome-dreampod.timer
systemctl --user enable autonome-discovery.timer

# 3. Start
./autonome-orchestrator.sh start

# 4. Monitor
./autonome-orchestrator.sh status
./autonome-orchestrator.sh logs
```

## Modules

| Module | File | Purpose |
|--------|------|---------|
| Broker | `broker/alpaca_client.py` | Alpaca REST API, paper/live gate |
| Data | `data/bars.py` | BarStore (SQLite + deque), AlpacaDataFeed |
| Data | `data/vix_feed.py` | VIX fetcher (Yahoo Finance, 15min cache) |
| Data | `data/earnings.py` | Earnings calendar (Finnhub, 24h cache) |
| Strategy | `strategy/momentum_breakout.py` | EMA + volume breakout signals |
| Risk | `risk/risk_manager.py` | Kelly sizing, drawdown halt, vol halt, PDT guard |
| Risk | `risk/portfolio_heat.py` | Total portfolio heat + sector heat tracking |
| Execution | `execution/engine.py` | Native OCO bracket orders, lifecycle tracking |
| Execution | `execution/rate_limiter.py` | Token-bucket order throttling (6/min global, 2/min/symbol) |
| Execution | `execution/reconcile.py` | Broker position/order reconciliation |
| Execution | `execution/limit_entry.py` | Limit orders with market fallback |
| Journal | `journal/trade_journal.py` | SQLite append-only audit log + auto-rotation |
| Supervisor | `supervisor/main.py` | 24x7 loop, API failure hard stop, stale data guard |
| Review | `supervisor/review.py` | LLM cockpit dashboard |
| LLM Gate | `intelligence/llm_gate.py` | Qualitative signal review (APPROVE/REJECT/MODIFY) |
| DreamPod | `intelligence/dreampod.py` | Overnight analysis, regime detection |
| Discovery | `intelligence/discovery.py` | News + supply-chain + corruption-aware discovery |
| Alerts | `alerts/telegram.py` | Real-time Telegram alerts for critical events |

## Safety Features

- **LIVE mode safety gate**: Requires `AUTONOME_LIVE_CONFIRM=I_UNDERSTAND` environment variable
- **Drawdown halt**: Persistent across restarts (saved to `data/halted.json`)
- **API failure hard stop**: 5 consecutive failures → auto-halt
- **Data staleness guard**: 3-cycle soft halt when bars older than 2 hours
- **Volatility halt**: Rejects signals when realized vol exceeds threshold or VIX >= 40
- **VIX sizing**: Position size halved when VIX >= 30
- **PDT guard**: Blocks day trades when count >= 3
- **Fractional shares**: Alpaca fractional support (no `int()` clamping)
- **OCO bracket orders**: Native Alpaca bracket API (not independent legs)
- **Short selling guards**: HTB rejection, margin check, 1.5x BP requirement
- **Order throttling**: Prevents Alpaca rate-limit hits (6/min global, 2/min/symbol)
- **Earnings avoidance**: Skips signals within ±2 days of earnings (Finnhub calendar)
- **Broker reconciliation**: Hourly position/order mismatch detection
- **Journal rotation**: Auto-archives when DB > 500MB
- **Portfolio heat guard**: Total heat <= 5% equity, sector heat <= 3%
- **Conviction weighting**: Position sizes scaled by signal confidence relative to portfolio

## Intelligence Layer

### LLM Gate
Every signal passes through an LLM review before execution. The LLM sees:
- Recent PnL for the symbol
- Current market regime
- Playbook alignment
- Macro context

Can `APPROVE`, `REJECT`, or `MODIFY` (entry/stop/target/qty).

### DreamPod
Runs at 04:00 UTC daily. Analyzes all symbols overnight:
- Multi-timeframe technical profiles
- Support/resistance levels
- Portfolio positioning recommendations
- Macro briefing from headlines

Outputs: `data/dreampod_briefing.json` + `data/dreampod_memo.md`

### Discovery Engine
Runs every 6 hours. Scans news for catalyst themes:
- Maps themes to supply chain beneficiaries
- Corruption heuristics for emerging markets (Adani/Ambani/Gadkari nexus)
- LLM fallback for unknown themes
- Ranks candidates by catalyst strength + political nexus

Outputs: `data/discovery_briefing.json` + `data/discovery_memo.md`

## Supply Chain Maps

Thematic discovery uses `intelligence/supply_chain_maps.json`:
- `india_data_center_fdi` → ADANIGREEN.NS, RELIANCE.NS, LT.NS
- `india_semiconductor_fab` → VEDL.NS, DIXON.NS
- `us_ai_infrastructure` → VST, CEG, ANET, DLR
- `us_defense_ai` → PLTR, LMT, RTX
- `global_battery_supply_chain` → ALB, TSLA, MP

## Files

| Path | Purpose |
|------|---------|
| `config/settings.yaml` | Runtime config (mode, symbols, risk params) |
| `config/secrets.yaml` | API keys (git-ignored) |
| `config/playbook.md` | AI trading playbook (regime, theses, watchlist) |
| `data/journal.sqlite` | Trade journal database |
| `data/state.json` | Live health dump |
| `data/gate_decisions.jsonl` | LLM Gate audit log |
| `data/dreampod_*.json/md` | Overnight analysis output |
| `data/discovery_*.json/md` | Discovery output |

## Tests

```bash
cd /mnt/e/NomadCrew[GROWTH]/trading-os/v2
python3 tests/test_pipeline.py
```

## Version History

- v2.0: Core deterministic system (Alpaca, momentum, Kelly, journal)
- v2.1: Intelligence layer (LLM Gate, DreamPod, Discovery Engine)
- v2.2: Safety layer (OCO brackets, API hard stop, vol halt, fractional shares, PDT guard)
- v2.3: Operational maturity (short guards, throttling, VIX feed, Telegram alerts, earnings guard, reconciliation, journal rotation, portfolio heat, LIVE mode gate)

## License

MIT
