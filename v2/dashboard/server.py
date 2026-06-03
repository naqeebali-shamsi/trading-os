#!/usr/bin/env python3
"""
dashboard/server.py — v1.0
Retro aircraft-cockpit HUD dashboard for the Autonome Trading OS.
Serves a CRT-phosphor themed UI with live data from journal.sqlite.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import sqlite3, json, os, re
from urllib.parse import urlparse
from datetime import datetime, timezone

PORT = 8765
DB = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/data/journal.sqlite"
LOG = "/tmp/autonome_paper.log"

INDEX_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Autonome Cockpit</title>
<style>
:root {
  --ret-phosphor: #00ff41; --ret-dim: #0b6e1c; --ret-faint: rgba(0,255,65,.1);
  --ret-amber: #ffb000; --ret-red: #ff0040; --ret-cyan: #00f0ff;
  --ret-bg: #050a05; --ret-grid: rgba(0,255,65,.06);
  --crt-scan: rgba(0,0,0,.35);
}
* {box-sizing:border-box; margin:0; padding:0; font-family: 'Courier New',monospace;}
html, body {height:100%; overflow:hidden;}
body {
  background: var(--ret-bg);
  color: var(--ret-phosphor);
}
body::before {
  content:''; position:fixed; inset:0; pointer-events:none; z-index:9999;
  background: repeating-linear-gradient(0deg, transparent, transparent 1px, var(--crt-scan) 1px, var(--crt-scan) 3px);
  background-size: 100% 4px;
}
body::after {
  content:''; position:fixed; inset:0; pointer-events:none; z-index:9998;
  background: radial-gradient(ellipse at center, transparent 50%, rgba(0,20,0,.7) 100%);
}
@keyframes flicker {0%{opacity:.95}50%{opacity:1}100%{opacity:.97}}
@keyframes radar-spin {from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes pulse {0%,100%{opacity:.5}50%{opacity:1}}
@keyframes blink {0%,49%{opacity:1}50%,100%{opacity:.3}}

.hud-container {position:relative; z-index:10; height:100vh; display:grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  grid-template-rows: 40px 1fr 1fr 1fr 20px;
  gap: 2px; padding: 4px;}

.hud-header {
  grid-column:1/-1; display:flex; align-items:center; justify-content:space-between;
  padding:0 12px; border-bottom:1px solid var(--ret-dim); background:rgba(0,255,65,.03);}
.hud-header h1 {font-size:14px; letter-spacing:2px; text-transform:uppercase;}
.hud-status {font-size:11px; display:flex; gap:12px; align-items:center;}
.status-dot {width:8px;height:8px;border-radius:50%;display:inline-block;animation:blink 1s infinite;}
.status-dot.ok {background:var(--ret-phosphor);box-shadow:0 0 8px var(--ret-phosphor);}
.status-dot.warn {background:var(--ret-amber);box-shadow:0 0 8px var(--ret-amber);animation:blink .5s infinite;}
.status-dot.err {background:var(--ret-red);box-shadow:0 0 8px var(--ret-red);animation:blink .3s infinite;}

.panel {
  border: 1px solid var(--ret-dim);
  background: var(--ret-faint);
  padding: 6px 8px;
  position: relative;
  overflow: hidden;
}
.panel::before {
  content:''; position:absolute; top:0; left:0; right:0; height:1px;
  background: linear-gradient(90deg, transparent, var(--ret-phosphor), transparent);
  opacity:.5;
}
.panel-title {
  font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px;
  color: var(--ret-dim); margin-bottom: 4px; border-bottom: 1px dashed var(--ret-dim);
  padding-bottom: 2px;
}
.panel-value {
  font-size: 24px; font-weight: bold; text-shadow: 0 0 10px var(--ret-phosphor);
}
.panel-value.negative {color:var(--ret-red); text-shadow:0 0 10px var(--ret-red);}
.panel-value.positive {color:var(--ret-phosphor);}
.panel-sub {font-size:10px; color:var(--ret-dim); margin-top:2px;}

/* Radar panel */
.radar-container {
  position: relative; width: 120px; height: 120px; margin: 8px auto;
  border: 1px solid var(--ret-dim); border-radius: 50%;
  background: radial-gradient(circle at center, rgba(0,255,65,.05) 0%, transparent 70%);
}
.radar-sweep {
  position: absolute; top: 0; left: 0; width: 100%; height: 100%;
  border-radius: 50%;
  background: conic-gradient(from 0deg, transparent 0deg, var(--ret-phosphor) 5deg, transparent 10deg);
  animation: radar-spin 3s linear infinite;
}
.radar-grid {
  position: absolute; inset: 10px; border: 1px solid rgba(0,255,65,.1);
  border-radius: 50%;
}
.radar-blip {
  position: absolute; width: 4px; height: 4px; border-radius: 50%;
  background: var(--ret-phosphor); box-shadow: 0 0 6px var(--ret-phosphor);
}

/* Scroll feed */
.feed-box {height: calc(100% - 30px); overflow-y: auto; font-size: 10px; line-height: 1.4;}
.feed-item {border-left: 2px solid var(--ret-dim); padding-left: 4px; margin-bottom: 3px;}
.feed-item.buy {border-color: var(--ret-phosphor);}
.feed-item.sell {border-color: var(--ret-amber);}
.feed-item.err {border-color: var(--ret-red);}
.feed-time {color: var(--ret-dim); font-size: 9px;}

/* Sparkline canvas */
canvas.spark {width:100%;height:50px;opacity:.8;}

/* Gauge bar */
.gauge-row {display:flex;align-items:center;gap:6px;margin:3px 0;font-size:10px;}
.gauge-track {flex:1;height:4px;background:rgba(0,255,65,.1);position:relative;}
.gauge-fill {height:100%;background:var(--ret-phosphor);box-shadow:0 0 4px var(--ret-phosphor);transition:width .5s;}
.gauge-fill.danger {background:var(--ret-red);box-shadow:0 0 4px var(--ret-red);}

/* Grid lines background */
.hud-container {
  background-image:
    linear-gradient(var(--ret-grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--ret-grid) 1px, transparent 1px);
  background-size: 40px 40px;
}

.panel-2x {grid-row:span 2;}
.panel-2w {grid-column:span 2;}
.footer {grid-column:1/-1; font-size:9px; color:var(--ret-dim); text-align:center; padding-top:3px;}
</style></head><body>
<div class="hud-container">
  <div class="hud-header">
    <h1>Autonome Cockpit v2.6</h1>
    <div class="hud-status">
      <span>MODE: <span id="mode">PAPER</span></span>
      <span>SYMBOL: <span id="symbol">TQQQ</span></span>
      <span>STATE: <span id="state" class="status-dot ok"></span> <span id="state-text">ACTIVE</span></span>
      <span>UPDATED: <span id="ts">--</span></span>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">Equity</div>
    <div class="panel-value" id="equity">$--</div>
    <div class="panel-sub" id="equity-change">--</div>
  </div>

  <div class="panel">
    <div class="panel-title">P&L Today</div>
    <div class="panel-value" id="pnl-today">$--</div>
    <div class="panel-sub" id="trade-count">-- trades</div>
  </div>

  <div class="panel">
    <div class="panel-title">Win Rate</div>
    <div class="panel-value" id="win-rate">--%</div>
    <div class="panel-sub" id="profit-factor">PF: --</div>
  </div>

  <div class="panel">
    <div class="panel-title">Signal Radar</div>
    <div class="radar-container">
      <div class="radar-sweep"></div>
      <div class="radar-grid"></div>
      <div class="radar-blip" id="blip1" style="top:30%;left:40%"></div>
      <div class="radar-blip" id="blip2" style="top:60%;left:70%;background:var(--ret-amber)"></div>
    </div>
    <div class="panel-sub" id="signals-today">-- signals</div>
  </div>

  <div class="panel panel-2w panel-2x">
    <div class="panel-title">Equity Sparkline</div>
    <canvas id="spark" class="spark" height="80"></canvas>
  </div>

  <div class="panel panel-2x">
    <div class="panel-title">Live Event Feed</div>
    <div class="feed-box" id="feed"><div class="feed-item"><span class="feed-time">--:--</span> Waiting for data...</div></div>
  </div>

  <div class="panel">
    <div class="panel-title">Drawdown</div>
    <div class="panel-value" id="drawdown">--%</div>
    <div class="gauge-row"><span>MAX</span><div class="gauge-track"><div class="gauge-fill" id="dd-bar" style="width:0%"></div></div></div>
  </div>

  <div class="panel">
    <div class="panel-title">Open Positions</div>
    <div id="positions" style="font-size:10px;">None</div>
  </div>

  <div class="panel">
    <div class="panel-title">Health</div>
    <div style="font-size:9px; line-height:1.5; color:var(--ret-dim);">
      <div id="health-log">OK</div>
      <div id="health-last">--</div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">Strategy</div>
    <div style="font-size:10px; line-height:1.4;">
      <div>Router: <span id="strat-router">--</span></div>
      <div>Momentum: <span id="strat-mom">--</span></div>
      <div>Pullback: <span id="strat-pull">--</span></div>
    </div>
  </div>

  <div class="panel panel-2w">
    <div class="panel-title">Recent Trades</div>
    <div style="font-size:9px; overflow:auto; max-height:90px;" id="recent-trades">--</div>
  </div>

  <div class="panel">
    <div class="panel-title">Portfolio Heat</div>
    <div class="gauge-row"><span>RISK</span><div class="gauge-track"><div class="gauge-fill" id="heat-bar" style="width:0%"></div></div></div>
    <div class="panel-sub" id="heat-text">0% / 5%</div>
  </div>

  <div class="footer">AUTONOME TRADING OS v2.6 | ALPACA PAPER | 24/7 AUTONOMOUS | SYS HEALTH OK</div>
</div>

<script>
const fmtMoney = (n) => n === null || n === undefined || isNaN(n) ? '--' : '$' + n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtPct = (n) => n === null || n === undefined || isNaN(n) ? '--%' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%';

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    document.getElementById('ts').textContent = new Date().toLocaleTimeString();
    document.getElementById('equity').textContent = fmtMoney(d.equity);
    const ec = document.getElementById('equity-change');
    ec.textContent = fmtPct(d.equity_change_pct);
    ec.className = 'panel-sub ' + (d.equity_change_pct >= 0 ? 'positive' : 'negative');
    document.getElementById('pnl-today').textContent = fmtMoney(d.pnl_today);
    document.getElementById('pnl-today').className = 'panel-value ' + (d.pnl_today >= 0 ? 'positive' : 'negative');
    document.getElementById('trade-count').textContent = (d.trades_today || 0) + ' trades';
    document.getElementById('win-rate').textContent = fmtPct(d.win_rate || 0);
    document.getElementById('profit-factor').textContent = 'PF: ' + (d.profit_factor ? d.profit_factor.toFixed(2) : '--');
    document.getElementById('drawdown').textContent = fmtPct(d.drawdown || 0);
    document.getElementById('dd-bar').style.width = Math.min((d.drawdown || 0) * 5, 100) + '%';
    document.getElementById('dd-bar').className = 'gauge-fill' + ((d.drawdown || 0) > 5 ? ' danger' : '');
    document.getElementById('signals-today').textContent = (d.signals_today || 0) + ' signals today';
    document.getElementById('positions').innerHTML = (d.positions || []).map(p => `<div>${p.symbol} ${p.side} ${p.qty} @ ${p.unrealized}</div>`).join('') || 'None';
    document.getElementById('health-log').textContent = d.health_status || 'OK';
    document.getElementById('health-last').textContent = d.health_last || '--';
    document.getElementById('strat-router').textContent = (d.strategies || {}).router || '--';
    document.getElementById('strat-mom').textContent = (d.strategies || {}).momentum || '--';
    document.getElementById('strat-pull').textContent = (d.strategies || {}).pullback || '--';
    document.getElementById('state-text').textContent = d.swarm_state || 'ACTIVE';
    const st = document.getElementById('state');
    st.className = 'status-dot ' + (d.swarm_state === 'ACTIVE' ? 'ok' : d.swarm_state === 'PAUSED' ? 'warn' : 'err');

    // Heat bar
    const heat = d.heat_pct || 0;
    document.getElementById('heat-bar').style.width = Math.min(heat * 20, 100) + '%';
    document.getElementById('heat-bar').className = 'gauge-fill' + (heat > 4 ? ' danger' : '');
    document.getElementById('heat-text').textContent = heat.toFixed(1) + '% / 5%';

    // Sparkline
    if (d.equity_history && d.equity_history.length > 1) {
      drawSpark(d.equity_history);
    }

    // Recent trades
    if (d.recent_trades) {
      document.getElementById('recent-trades').innerHTML = d.recent_trades.map(t =>
        `<div style="${t.pnl >= 0 ? 'color:#00ff41' : 'color:#ff0040'}">${t.t} ${t.symbol} ${t.side} ${(t.pnl||0).toFixed(2)}</div>`
      ).join('') || '--';
    }

    // Feed
    if (d.events) {
      const fb = document.getElementById('feed');
      fb.innerHTML = d.events.slice(0, 20).map(e => {
        const cls = e.type === 'buy' ? 'buy' : e.type === 'sell' ? 'sell' : e.type === 'err' ? 'err' : '';
        return `<div class="feed-item ${cls}"><span class="feed-time">${e.t}</span> ${e.msg}</div>`;
      }).join('');
      fb.scrollTop = 0;
    }
  } catch(e) { console.error('refresh failed', e); }
}

function drawSpark(arr) {
  const c = document.getElementById('spark');
  const ctx = c.getContext('2d');
  c.width = c.clientWidth * 2; c.height = c.clientHeight * 2;
  const w = c.width, h = c.height;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = '#00ff41'; ctx.lineWidth = 2;
  const min = Math.min(...arr), max = Math.max(...arr), range = max - min || 1;
  ctx.beginPath();
  arr.forEach((v,i) => {
    const x = (i / (arr.length - 1)) * w;
    const y = h - ((v - min) / range) * h * 0.8 - h * 0.1;
    i === 0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
  });
  ctx.stroke();
  ctx.fillStyle = 'rgba(0,255,65,.1)';
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath(); ctx.fill();
}

setInterval(refresh, 5000);
refresh();
</script></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        SimpleHTTPRequestHandler.end_headers(self)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode())
            return

        if parsed.path == "/api/status":
            data = self._build_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
            return

        self.send_response(404)
        self.end_headers()

    def _build_status(self):
        """Pull live data from journal.sqlite + supervisor log."""
        out = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": 100000.0,
            "equity_change_pct": 0.0,
            "pnl_today": 0.0,
            "trades_today": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "drawdown": 0.0,
            "signals_today": 0,
            "positions": [],
            "health_status": "OK",
            "health_last": "--",
            "swarm_state": "ACTIVE",
            "heat_pct": 0.0,
            "equity_history": [],
            "recent_trades": [],
            "events": [],
            "strategies": {"router": "--", "momentum": "--", "pullback": "--"},
        }

        # Read sqlite
        if os.path.exists(DB):
            try:
                import sqlite3
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with sqlite3.connect(DB) as db:
                    # Equity
                    row = db.execute("SELECT equity, drawdown FROM equity ORDER BY t DESC LIMIT 1").fetchone()
                    if row:
                        out["equity"] = row[0] or 100000.0
                        out["drawdown"] = row[1] or 0.0

                    # Equity history
                    rows = db.execute("SELECT equity FROM equity ORDER BY t DESC LIMIT 50").fetchall()
                    out["equity_history"] = [r[0] for r in reversed(rows)]

                    # PnL today
                    row = db.execute("SELECT COUNT(*), SUM(pnl) FROM pnl WHERE t LIKE ?", (today + "%",)).fetchone()
                    out["trades_today"] = row[0] or 0
                    out["pnl_today"] = row[1] or 0.0

                    # Win rate today
                    row = db.execute("SELECT COUNT(*) FROM pnl WHERE t LIKE ? AND pnl > 0", (today + "%",)).fetchone()
                    wins = row[0] or 0
                    if out["trades_today"] > 0:
                        out["win_rate"] = (wins / out["trades_today"]) * 100

                    # Profit factor
                    row = db.execute("SELECT SUM(pnl) FROM pnl WHERE t LIKE ? AND pnl > 0", (today + "%",)).fetchone()
                    gross_profit = row[0] or 0.0
                    row = db.execute("SELECT SUM(ABS(pnl)) FROM pnl WHERE t LIKE ? AND pnl < 0", (today + "%",)).fetchone()
                    gross_loss = row[0] or 0.0
                    if gross_loss > 0:
                        out["profit_factor"] = gross_profit / gross_loss

                    # Signals today
                    row = db.execute("SELECT COUNT(*) FROM signals WHERE t LIKE ?", (today + "%",)).fetchone()
                    out["signals_today"] = row[0] or 0

                    # Heat
                    row = db.execute("SELECT positions FROM equity ORDER BY t DESC LIMIT 1").fetchone()
                    if row and row[0]:
                        out["heat_pct"] = (row[0] * 1000.0 / (out["equity"] or 100000.0)) * 100

                    # Recent trades
                    rows = db.execute("SELECT t, symbol, side, pnl FROM pnl ORDER BY t DESC LIMIT 10").fetchall()
                    out["recent_trades"] = [
                        {"t": r[0][-8:] if r[0] else "--", "symbol": r[1], "side": r[2], "pnl": r[3]}
                        for r in rows
                    ]
            except Exception as e:
                out["health_status"] = f"DB_ERROR: {e}"

        # Read log tail
        events = []
        if os.path.exists(LOG):
            try:
                with open(LOG, "r", errors="ignore") as f:
                    lines = f.readlines()[-50:]
                for line in lines:
                    m = re.search(r'(\d{2}:\d{2}:\d{2}).*?(Equity=|signal|trade|error|ERROR|CRITICAL)', line, re.I)
                    if m:
                        t = m.group(1)
                        msg = line.strip()[30:]  # strip timestamp
                        etype = "err" if "error" in line.lower() or "critical" in line.lower() else (
                            "buy" if "buy" in line.lower() else "sell" if "sell" in line.lower() else "info"
                        )
                        events.append({"t": t, "msg": msg[:120], "type": etype})
                out["events"] = list(reversed(events))
            except:
                pass

        # Swarm state
        state_path = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/config/swarm_state"
        if os.path.exists(state_path):
            with open(state_path) as f:
                out["swarm_state"] = f.read().strip()

        return out


if __name__ == "__main__":
    print(f"Autonome Cockpit serving on http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
