# MT5 Chart Bootstrap

Trading OS discovers MT5 charts via `ipc/chart_{SYMBOL}/` heartbeats. The OS does not attach
Expert Advisors by itself — **`ChartBootstrapService`** opens missing charts and applies a bridge
template that includes `FileBridgeEA_MultiSymbol`.

## One-time bridge reference chart

1. Open MetaEditor and compile:
   - `tracks/track_b_multisymbol/FileBridgeEA_MultiSymbol.mq5`
   - `bridge/ChartBootstrapService.mq5` (**recompile after updates**)
2. In MT5, open any symbol chart (e.g. EURUSD M15).
3. Attach **FileBridgeEA_MultiSymbol**, set Algo Trading ON, confirm `ipc/chart_EURUSD/` appears.

Optional manual template (only if auto-export fails):

4. **Right-click chart → Template → Save Template** as `trading_os_bridge`  
   (must land in `MQL5/Profiles/Templates/trading_os_bridge.tpl`)

If you previously saved `trading_os_bridge.tpl`, run:

```bash
python scripts/install_mt5_bridge_template.py --install
```

The bootstrap service applies that template to every chart it opens. v1.01+ can auto-export
the template from your working EURUSD bridge chart when the tpl file is missing.

## Generate manifest (Python side)

```bash
python scripts/bootstrap_mt5_charts.py --write
```

Writes:

| File | Purpose |
|------|---------|
| `ipc/chart_manifest.csv` | Read by MT5 from `FILE_COMMON/trading-os/chart_manifest.csv` |
| `config/chart_manifest.json` | Human-readable manifest for dashboard/agents |

Re-run after changing `config/instruments.yaml` (e.g. `scripts/enable_stock_trading.py` also regenerates).

## Run bootstrap service (MT5 side)

1. Copy or symlink the repo `ipc/` folder into MT5 **Common Files** as `trading-os/`  
   (same layout used by the bridge EA: heartbeats land in `trading-os/chart_EURUSD/`, etc.).
2. Attach **ChartBootstrapService** to any chart.
3. Enable **Algo Trading**.

The service timer (default 300s) reads the manifest, skips charts with fresh heartbeats,
opens missing symbols via `ChartOpen`, and applies `trading_os_bridge.tpl`.

Logs: `FILE_COMMON/trading-os/chart_bootstrap.log`

## Gap report

```bash
python scripts/bootstrap_mt5_charts.py --json
```

HTTP: `GET /api/chart/bootstrap?max_heartbeat_age=120`

Agent bundle: `chart_bootstrap` in `GET /api/agent/context`.

Exit code is non-zero when any enabled symbol is **MISSING** or **STALE**.

## Readiness integration

When the readiness gate fails due to missing charts, it prints bootstrap actions from the gap
report (`scripts/bootstrap_mt5_charts.py --write` + attach `ChartBootstrapService.ex5`).

## Architecture

```
instruments.yaml → chart_bootstrap.py → ipc/chart_manifest.csv
                                              ↓
                              ChartBootstrapService.mq5 (MT5)
                                              ↓
                         chart_{SYMBOL}/ + FileBridgeEA_MultiSymbol
                                              ↓
                              Python bridge_status / preflight READY
```
