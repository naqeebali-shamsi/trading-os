function asText(value) {
  if (value === null || value === undefined) return "Not available";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function emptyState(message) {
  return `<div class="organism-empty-state">${asText(message)}</div>`;
}

function tableWrap(rowsHtml, { scroll = false } = {}) {
  const modifier = scroll ? " organism-table-wrap--scroll" : "";
  return `<div class="organism-table-wrap${modifier}"><table class="organism-table">${rowsHtml}</table></div>`;
}

function pillRow(...pills) {
  return `<div class="atom-row">${pills.join("")}</div>`;
}

function kvHtml(rows) {
  const content = rows
    .map(([k, v]) => `<dt>${k}</dt><dd>${asText(v)}</dd>`)
    .join("");
  return `<dl class="molecule-kv">${content}</dl>`;
}

function pill(label, variant) {
  return `<span class="atom-pill atom-pill--${variant}">${label}</span>`;
}

function formatTs(ts) {
  if (!ts) return "";
  return new Date(Number(ts) * 1000).toLocaleTimeString();
}

function formatPct(value) {
  if (value === null || value === undefined) return "Not available";
  const num = Number(value);
  if (Number.isNaN(num)) return asText(value);
  return `${(num * 100).toFixed(1)}%`;
}

function formatMoney(value) {
  if (value === null || value === undefined) return "Not available";
  const num = Number(value);
  if (Number.isNaN(num)) return asText(value);
  const sign = num >= 0 ? "+" : "";
  return `${sign}${num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pnlClass(value) {
  const num = Number(value || 0);
  if (Number.isNaN(num) || num === 0) return "";
  return num >= 0 ? "positive" : "negative";
}

function assetLabel(value) {
  const key = String(value || "").toLowerCase();
  if (key === "stock_cfd") return "Stock";
  if (key === "fx") return "FX";
  if (key === "metal") return "Metal";
  if (key === "index") return "Index";
  return key ? key.toUpperCase() : "Other";
}

function readinessCount(trader, statusSummary) {
  if (trader?.readiness?.available) {
    return trader.readiness.ready_count;
  }
  const ready = statusSummary?.ready_symbols;
  if (Array.isArray(ready)) return ready.length;
  return null;
}

function formatStatusLine(updatedAt, trader, statusSummary, eventCount) {
  const ready = readinessCount(trader, statusSummary);
  const readyText = ready === null
    ? "Readiness check pending"
    : ready === 1
      ? "1 symbol ready to trade"
      : `${ready} symbols ready to trade`;
  const equity = trader?.portfolio_pnl?.account?.equity;
  const equityText = equity != null ? `Equity ${formatMoney(equity)}.` : "";
  return `Last updated ${updatedAt}. ${equityText} ${readyText}. ${eventCount} recent events.`.replace(/\s+/g, " ").trim();
}

function renderResearchWatchlist(panel) {
  if (!panel || !panel.available) {
    return emptyState(panel?.message || "Research will appear after the next daily scan.");
  }
  const picks = Array.isArray(panel.picks) ? panel.picks : [];
  if (picks.length === 0) {
    return emptyState(panel.message || "No names cleared the confidence threshold today.");
  }
  const rows = picks.map((pick) => {
    const conf = pick.confidence != null ? formatPct(pick.confidence) : "Not available";
    const tierVariant = pick.tier === "multibagger_candidate" ? "live" : pick.tier === "high_conviction" ? "safe" : "warn";
    return `
      <tr>
        <td><strong>${asText(pick.symbol)}</strong></td>
        <td>${pill(asText(pick.tier_label), tierVariant)}</td>
        <td>${conf}</td>
        <td><div class="organism-table-cell-clamp">${asText(pick.thesis)}</div></td>
      </tr>`;
  }).join("");
  return `
    <p class="atom-muted">${asText(panel.message)}</p>
    ${tableWrap(`<thead><tr><th>Symbol</th><th>Rating</th><th>Confidence</th><th>Why it ranked</th></tr></thead><tbody>${rows}</tbody>`, { scroll: true })}`;
}

function renderPortfolioPnlPanel(panel) {
  if (!panel?.available) {
    return emptyState(panel?.message || "Portfolio metrics are not available yet.");
  }
  const account = panel.account || {};
  const pnl = panel.pnl || {};
  const exposure = panel.exposure || {};
  const bySymbol = Array.isArray(exposure.by_symbol) ? exposure.by_symbol : [];
  const curve = panel.equity_curve || {};
  const curvePoints = Array.isArray(curve.points) ? curve.points : [];
  const sparkline = curve.available && curvePoints.length
    ? `
    <div class="molecule-equity-sparkline">
      <div class="molecule-equity-sparkline-header">
        <span class="molecule-stat-label">Equity curve</span>
        <span id="portfolio-equity-sparkline-meta" class="molecule-equity-sparkline-meta"></span>
      </div>
      <div class="molecule-equity-sparkline-canvas">
        <canvas id="portfolio-equity-sparkline" aria-label="Equity sparkline"></canvas>
      </div>
    </div>`
    : "";
  const stats = `
    <div class="molecule-panel-section">
      <div class="molecule-stat-row">
        <div class="molecule-stat">
          <div class="molecule-stat-label">Equity</div>
          <div class="molecule-stat-value">${formatMoney(account.equity)}</div>
        </div>
        <div class="molecule-stat">
          <div class="molecule-stat-label">Balance</div>
          <div class="molecule-stat-value">${formatMoney(account.balance)}</div>
        </div>
        <div class="molecule-stat">
          <div class="molecule-stat-label">Floating P&amp;L</div>
          <div class="molecule-stat-value ${pnlClass(pnl.floating_pnl)}">${formatMoney(pnl.floating_pnl)}</div>
        </div>
        <div class="molecule-stat">
          <div class="molecule-stat-label">Realized today</div>
          <div class="molecule-stat-value ${pnlClass(pnl.realized_today)}">${formatMoney(pnl.realized_today)}</div>
        </div>
        <div class="molecule-stat">
          <div class="molecule-stat-label">Total P&amp;L</div>
          <div class="molecule-stat-value ${pnlClass(pnl.total_pnl)}">${formatMoney(pnl.total_pnl)}</div>
        </div>
        <div class="molecule-stat">
          <div class="molecule-stat-label">Invested notional</div>
          <div class="molecule-stat-value">${formatMoney(exposure.invested_notional)}</div>
        </div>
      </div>
    </div>`;
  const meta = `
    <div class="molecule-panel-section">
      <p class="atom-muted">${asText(panel.message)}</p>
      ${kvHtml([
        ["Open legs", exposure.open_count ?? 0],
        ["Return on balance", pnl.return_pct != null ? `${Number(pnl.return_pct).toFixed(2)}%` : "Not available"],
        ["Margin free", formatMoney(account.margin_free)],
        ["Source", panel.source || "unknown"],
      ])}
    </div>`;
  if (!bySymbol.length) {
    return `${stats}${sparkline}${meta}`;
  }
  const rows = bySymbol.map((row) => `
    <tr>
      <td><strong>${asText(row.symbol)}</strong></td>
      <td>${asText(row.open_count)}</td>
      <td>${formatMoney(row.notional)}</td>
      <td class="${pnlClass(row.floating_pnl)}">${formatMoney(row.floating_pnl)}</td>
    </tr>`).join("");
  return `${stats}${sparkline}${meta}
    <div class="molecule-panel-section">
      ${tableWrap(`<thead><tr><th>Symbol</th><th>Legs</th><th>Notional</th><th>Floating P&amp;L</th></tr></thead><tbody>${rows}</tbody>`, { scroll: true })}
    </div>`;
}

function renderPositionsPanel(panel) {
  if (!panel?.available) {
    return emptyState(panel?.message || "Position data is not available yet.");
  }
  const pnl = Number(panel.floating_pnl || 0);
  const pnlTone = pnl >= 0 ? "positive" : "negative";
  const positions = Array.isArray(panel.positions) ? panel.positions : [];
  let body = `
    <div class="molecule-stat-row">
      <div class="molecule-stat">
        <div class="molecule-stat-label">Open positions</div>
        <div class="molecule-stat-value">${panel.open_count || 0}</div>
      </div>
      <div class="molecule-stat">
        <div class="molecule-stat-label">Floating P&amp;L</div>
        <div class="molecule-stat-value ${pnlTone}">${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}</div>
      </div>
    </div>`;
  if (positions.length === 0) {
    return `${body}${emptyState("You have no open positions.")}`;
  }
  const rows = positions.map((pos) => `
    <tr>
      <td><strong>${asText(pos.symbol)}</strong></td>
      <td>${asText(pos.side)}</td>
      <td>${asText(pos.qty)}</td>
      <td>${asText(pos.open_price)}</td>
      <td>${asText(pos.current_price)}</td>
      <td class="${Number(pos.profit || 0) >= 0 ? "positive" : "negative"}">${Number(pos.profit || 0).toFixed(2)}</td>
    </tr>`).join("");
  return `${body}
    ${tableWrap(`<thead><tr><th>Symbol</th><th>Side</th><th>Size</th><th>Entry</th><th>Last</th><th>P&amp;L</th></tr></thead><tbody>${rows}</tbody>`, { scroll: true })}`;
}

function directionVariant(direction) {
  const value = String(direction || "").toLowerCase();
  if (["up", "buy", "bullish", "long"].includes(value)) return "safe";
  if (["down", "sell", "bearish", "short"].includes(value)) return "warn";
  return "neutral";
}

function formatForecastPath(pathValue) {
  if (pathValue == null || pathValue === "") return "—";
  if (Array.isArray(pathValue)) {
    const nums = pathValue.map((v) => Number(v)).filter((v) => Number.isFinite(v));
    if (!nums.length) return "—";
    if (nums.length === 1) return nums[0].toFixed(5);
    return `${nums[0].toFixed(5)} → ${nums[nums.length - 1].toFixed(5)} (${nums.length} pts)`;
  }
  const num = Number(pathValue);
  return Number.isFinite(num) ? num.toFixed(5) : asText(pathValue);
}

function stalenessVariant(staleness) {
  if (staleness === "fresh") return "safe";
  if (staleness === "aging") return "live";
  if (staleness === "stale") return "warn";
  return "neutral";
}

function directionArrow(direction) {
  const value = String(direction || "").toLowerCase();
  if (["up", "buy", "bullish", "long"].includes(value)) return "\u2191";
  if (["down", "sell", "bearish", "short"].includes(value)) return "\u2193";
  return "\u2192";
}

function formatForecastHistory(history) {
  if (!Array.isArray(history) || history.length <= 1) return "";
  const trail = history
    .slice(0, 6)
    .map((item) => directionArrow(item.direction))
    .join(" ");
  return `<br><span class="atom-muted">Recent: ${trail}</span>`;
}

function renderForecastThesisPanel(panel) {
  if (!panel || !panel.available) {
    return emptyState(panel?.message || "Forecasts will appear after candle data produces model context.");
  }
  const rows = Array.isArray(panel.rows) ? panel.rows : [];
  if (!rows.length) {
    return emptyState(panel.message || "No recent forecasts in the current window.");
  }
  const body = rows.slice(0, 12).map((row) => {
    const direction = row.direction || row.forecast?.direction || "flat";
    const variant = directionVariant(direction);
    const conf = row.confidence != null ? formatPct(row.confidence) : "Not available";
    const staleness = row.staleness && row.staleness !== "fresh"
      ? ` ${pill(asText(row.staleness_label || row.staleness), stalenessVariant(row.staleness))}`
      : "";
    const conflict = row.macro_conflict?.conflict
      ? `<br>${pill(asText(row.macro_conflict.label), row.macro_conflict.severity === "high" ? "live" : "warn")}`
      : "";
    const research = row.research
      ? `<br><span class="atom-muted">${asText(row.research.tier_label || row.research.tier)} ${row.research.confidence != null ? formatPct(row.research.confidence) : ""}</span>`
      : "";
    const macroContext = row.macro_news || row.macro;
    const macro = macroContext
      ? `<br><span class="atom-muted">${asText(macroContext.recommendation_label || macroContext.recommendation)}${macroContext.relevance != null ? ` (${formatPct(macroContext.relevance)})` : ""}</span>`
      : "";
    const status = row.error ? `<br>${pill(asText(row.error), "warn")}` : "";
    return `
      <tr>
        <td><strong>${asText(row.symbol)}</strong><br><span class="atom-muted">${asText(row.timeframe)} ${formatTs(row.ts)}</span>${staleness}</td>
        <td>${pill(asText(direction), variant)}${conflict}${research}${macro}${status}</td>
        <td>${conf}<br><span class="atom-muted">${asText(row.model || "model unknown")}</span>${formatForecastHistory(row.history)}</td>
        <td>${asText(row.last_close)}</td>
        <td><div class="organism-table-cell-clamp">${formatForecastPath(row.predicted_close || row.forecast?.predicted_close)}</div></td>
        <td><div class="organism-table-cell-clamp">${asText(row.thesis || row.research?.thesis || "No thesis attached")}</div></td>
      </tr>`;
  }).join("");
  const meta = [
    pill(`${rows.length} symbol views`, "safe"),
    panel.advisory_only === false ? pill("May influence orders", "warn") : pill("Advisory context", "safe"),
  ];
  if (panel.conflict_count) {
    meta.push(pill(`${panel.conflict_count} macro conflict${panel.conflict_count === 1 ? "" : "s"}`, "warn"));
  }
  if (panel.stale_count) {
    meta.push(pill(`${panel.stale_count} stale`, "neutral"));
  }
  if (panel.macro_summary?.recommendation_label) {
    meta.push(pill(panel.macro_summary.recommendation_label, directionVariant(panel.macro_summary.assessment)));
  }
  return `
    ${pillRow(...meta)}
    <p class="atom-muted">${asText(panel.message || "Latest forecast context from the live bus.")}</p>
    ${tableWrap(`<thead><tr><th>Symbol</th><th>View</th><th>Confidence</th><th>Last</th><th>Path</th><th>Thesis</th></tr></thead><tbody>${body}</tbody>`, { scroll: true })}`;
}

function renderEdgeValidationPanel(panel) {
  if (!panel?.available) {
    return emptyState(panel?.message || "Edge validation report not available yet.");
  }
  const groups = Array.isArray(panel.groups) ? panel.groups : [];
  if (!groups.length) {
    return emptyState(panel.message || "No labelled edge groups yet.");
  }
  const meta = [
    pill(`${panel.promotable_count || 0} promotable`, panel.promotable_count ? "safe" : "warn"),
    pill(`${panel.group_count || groups.length} groups`, "neutral"),
    pill(`${panel.label_count || 0} labels`, "neutral"),
  ];
  const rows = groups.slice(0, 20).map((row) => {
    const variant = row.promotable ? "safe" : "warn";
    const reasons = (row.reasons || []).length ? (row.reasons || []).join(", ") : "cleared";
    const pf = row.profit_factor;
    const pfText = pf != null && Number.isFinite(Number(pf)) ? Number(pf).toFixed(2) : "∞";
    return `
      <tr>
        <td><strong>${asText(row.symbol)}</strong><br><span class="atom-muted">${asText(row.timeframe)}</span></td>
        <td>${pill(row.promotable ? "Promotable" : "Hold", variant)}</td>
        <td>${asText(row.samples)}</td>
        <td>${row.win_rate != null ? formatPct(row.win_rate) : "—"}</td>
        <td>${row.edge != null ? Number(row.edge).toFixed(4) : "—"}</td>
        <td>${pfText}</td>
        <td>${asText(reasons)}</td>
      </tr>`;
  }).join("");
  return `
    ${pillRow(...meta)}
    <p class="atom-muted">${asText(panel.message)}</p>
    ${tableWrap(`<thead><tr><th>Symbol</th><th>Gate</th><th>Samples</th><th>Win rate</th><th>Edge</th><th>PF</th><th>Reasons</th></tr></thead><tbody>${rows}</tbody>`, { scroll: true })}`;
}

function renderMacroNewsPanel(panel) {
  if (!panel) return emptyState("No news or macro context in the current window.");
  const riskVariant = panel.risk_level === "elevated" ? "live" : panel.risk_level === "caution" ? "warn" : "safe";
  const affected = (panel.affected_symbols || [])
    .map((row) => `${row.symbol} (${Math.round(Number(row.relevance || 0) * 100)}%)`)
    .join(", ");
  const halts = (panel.halt_symbols || []).join(", ");
  const headlines = (panel.headlines || []).slice(0, 5).map((item) =>
    `<li><span class="organism-feed-ts">${formatTs(item.ts)}</span> ${asText(item.route)}: ${asText(item.title)}</li>`
  ).join("");
  const keywords = (panel.top_keywords || []).map((kw) => pill(kw, "warn")).join("");
  const impact = panel.impact_score ?? panel.confidence;
  return `
    ${pillRow(pill(asText(panel.risk_label || "Normal conditions"), riskVariant), pill(asText(panel.recommendation_label), "safe"))}
    <p class="atom-muted">Mood: <strong>${asText(panel.assessment)}</strong> · Impact ${impact != null ? asText(impact) : "N/A"}</p>
    ${affected ? `<p><span class="atom-label">Symbols in focus:</span> ${affected}</p>` : ""}
    ${halts ? `<p><span class="atom-label">Paused symbols:</span> ${halts}</p>` : ""}
    ${keywords ? `<div class="atom-label">Keywords</div>${pillRow(keywords)}` : ""}
    ${headlines ? `<ul class="organism-headline-list">${headlines}</ul>` : emptyState("No routed headlines in the recent feed.")}`;
}

function renderSignalDrilldown(panel) {
  if (!panel) return emptyState("No signal evaluations yet.");
  const recent = Array.isArray(panel.recent) ? panel.recent : [];
  if (recent.length === 0) {
    return `<p class="atom-muted">${asText(panel.headline)}</p>${emptyState("The signal engine has not evaluated a setup in this window.")}`;
  }
  const rows = recent.slice(0, 12).map((row) => {
    const variant = row.status === "passed" ? "safe" : row.status === "blocked" ? "warn" : "live";
    const patterns = Array.isArray(row.patterns) ? row.patterns.join(", ") : "";
    const research = row.research
      ? `<br><span class="atom-muted">Research: ${asText(row.research.tier_label || row.research.tier)}</span>`
      : "";
    const conf = row.confidence != null ? formatPct(row.confidence) : "";
    const minConf = row.min_confidence != null ? ` (min ${formatPct(row.min_confidence)})` : "";
    return `
      <tr class="${row.status === "passed" ? "is-ready" : "is-blocked"}">
        <td>${formatTs(row.ts)}</td>
        <td><strong>${asText(row.symbol)}</strong><br><span class="atom-muted">${asText(row.timeframe)}</span></td>
        <td>${pill(asText(row.stage_label || row.stage), variant)}</td>
        <td>${asText(row.reason_label || row.reason)}${research}</td>
        <td>${conf}${minConf}</td>
        <td>${asText(patterns || "None")}</td>
      </tr>`;
  }).join("");
  return `
    <p><strong>${asText(panel.headline)}</strong></p>
    <p class="atom-muted">${panel.passed_count || 0} passed · ${panel.blocked_count || 0} blocked</p>
    ${tableWrap(`<thead><tr><th>Time</th><th>Symbol</th><th>Step</th><th>Outcome</th><th>Conf.</th><th>Patterns</th></tr></thead><tbody>${rows}</tbody>`, { scroll: true })}`;
}

function renderReadinessPanel(panel) {
  if (!panel?.available) {
    return emptyState(panel?.message || "Symbol readiness could not be loaded.");
  }
  const rows = Array.isArray(panel.rows) ? panel.rows : [];
  if (rows.length === 0) {
    return emptyState(panel.message);
  }
  const body = rows.map((row) => `
    <tr class="${row.ready ? "is-ready" : "is-blocked"}">
      <td><strong>${asText(row.symbol)}</strong></td>
      <td>${assetLabel(row.asset_class)}</td>
      <td>${asText(row.status_label)}</td>
      <td>${asText(row.session)}</td>
      <td>${asText(row.spread)}</td>
      <td>${asText(row.quote)}</td>
      <td>${asText(row.chart)}</td>
    </tr>`).join("");
  return `
    <p class="atom-muted">${asText(panel.message)}</p>
    ${tableWrap(`<thead><tr><th>Symbol</th><th>Market</th><th>Status</th><th>Session</th><th>Spread</th><th>Quote</th><th>Chart</th></tr></thead><tbody>${body}</tbody>`, { scroll: true })}`;
}

async function promotionAction(action, promoId) {
  const response = await fetch(`/api/promotions/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: promoId, actor: "dashboard" }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `Promotion ${action} failed`);
  }
  return payload;
}

function renderPendingPromotions(panel) {
  if (!panel?.available) {
    return emptyState(panel?.message || "Promotion queue unavailable. Restart the dashboard.");
  }
  const items = Array.isArray(panel.items) ? panel.items : [];
  if (!items.length) {
    return emptyState(panel.message || "No pending improvements.");
  }
  return `<div class="organism-promotion-list">${items.map((item) => `
    <div class="organism-promotion-item" data-promo-id="${asText(item.id)}">
      ${pillRow(pill(asText(item.risk || "low"), item.risk === "medium" ? "warn" : "safe"), pill(asText(item.type), "live"))}
      <p><strong>${asText(item.summary)}</strong></p>
      <p class="atom-muted">Agent: ${asText(item.agent)} · ${formatTs(item.created_ts)}</p>
      <div class="molecule-button-row">
        <button type="button" class="atom-button atom-button--primary" data-promotion-action="approve" data-promo-id="${asText(item.id)}">Approve</button>
        <button type="button" class="atom-button" data-promotion-action="reject" data-promo-id="${asText(item.id)}">Reject</button>
      </div>
    </div>
  `).join("")}</div>`;
}

function renderDreamLabStatus(panel) {
  if (!panel?.available) {
    return emptyState(panel?.message || "Dream Lab status unavailable. Restart the dashboard.");
  }
  const cycle = panel.last_cycle?.payload || {};
  const policy = panel.live_policy || {};
  return `
    <p>${asText(panel.message)}</p>
    ${kvHtml([
      ["Last cycle", cycle.cycle || "None yet"],
      ["Agents", Array.isArray(cycle.agents) ? cycle.agents.join(", ") : "None"],
      ["Policy version", policy.version ?? "0"],
      ["Strategy overrides", policy.strategy_count ?? 0],
      ["Min confidence override", policy.signal_min_confidence ?? "Default"],
    ])}
  `;
}

function renderSafetyFlags(flags) {
  return `
    ${pillRow(
      flags.is_live_mode ? pill("Live account", "live") : pill("Demo mode", "safe"),
      flags.allow_mock_llm ? pill("Simulated AI", "warn") : pill("Live AI", "safe"),
      flags.allow_mock_forecasts ? pill("Simulated forecasts", "warn") : pill("Live forecasts", "safe"),
      flags.stop_trading_file ? pill("Emergency stop", "live") : pill("Trading enabled", "safe"),
    )}
    ${kvHtml([
      ["Account mode", flags.effective_mode],
      ["AI role", flags.llm_decision_mode],
      ["Risk profile", flags.risk_limits_mode],
    ])}
  `;
}

function renderTelemetry(summary) {
  const health = summary.health || {};
  const metrics = summary.metrics || {};
  const uptime = metrics.uptime_sec != null ? `${Math.round(metrics.uptime_sec)} sec` : "Not available";
  return `
    ${pillRow(summary.reachable ? pill("Metrics online", "safe") : pill("Metrics offline", "warn"))}
    ${kvHtml([
      ["Active charts", health.charts_alive],
      ["Recent bus events", health.bus ? health.bus.total_recent : "Not available"],
      ["Uptime", uptime],
      ["Orders filled", metrics.orders_filled_total],
      ["Orders queued", metrics.orders_queued_total],
    ])}
  `;
}

function renderSystemHealth(state) {
  const health = state.health || {};
  const alerts = Array.isArray(health.alerts) ? health.alerts.length : 0;
  return kvHtml([
    ["Overall health", health.ok ? "Good" : "Needs attention"],
    ["Open alerts", alerts],
    ["Active strategies", Array.isArray(state.strategies) ? state.strategies.length : "Not available"],
    ["Server time", new Date((state.ts || 0) * 1000).toLocaleString()],
  ]);
}

function renderBridgeStatus(status) {
  const root = status.root || {};
  const chartCount = Array.isArray(status.charts) ? status.charts.length : 0;
  const connected = status.connected;
  const modeLabel = String(status.mode || "unknown").replace(/_/g, " ");
  return `
    ${pillRow(
      connected ? pill("Connected", "safe") : pill("Not connected", "warn"),
      pill(modeLabel, connected ? "safe" : "warn"),
    )}
    ${kvHtml([
      ["Status", status.detail],
      ["Fresh charts", status.fresh_chart_count],
      ["Stale charts", status.stale_chart_count],
      ["Charts detected", chartCount],
      ["Last heartbeat (sec)", root.heartbeat_age_sec != null ? root.heartbeat_age_sec : "Not available"],
      ["Price feed", root.tick_ok ? "OK" : "Missing"],
    ])}
  `;
}

function humanTopic(topic) {
  const map = {
    "market.tick": "Price update",
    "market.regime": "Market regime",
    "market.signal": "Trade signal",
    "market.signal.blocked": "Signal blocked",
    "market.signal.candidate": "Signal candidate",
    "market.signal.evaluation": "Signal check",
    "muscle.order.intent": "Order intent",
    "muscle.order.sent": "Order sent",
    "muscle.order.filled": "Order filled",
    "muscle.order.rejected": "Order rejected",
    "portfolio.equity": "Portfolio equity",
    "position.pnl": "Position P&L",
    "immune.block": "Risk block",
    "immune.pass": "Risk approved",
    "cortex.decision": "AI decision",
    "rd.dream.cycle.start": "Dream cycle start",
    "rd.dream.cycle.complete": "Dream cycle done",
    "rd.promotion.proposed": "R&D proposal",
    "rd.agent.task": "R&D agent task",
    "rd.agent.result": "R&D agent result",
    "memory.learner.action": "Learner action",
    "research.stock.ranked": "Stock research",
  };
  if (map[topic]) return map[topic];
  if (String(topic || "").startsWith("rd.")) return topic.replace(/^rd\./, "Dream Lab · ");
  if (String(topic || "").startsWith("macro.news.")) return "Macro news";
  return topic || "Event";
}

function topicMatches(topic, filters) {
  if (!filters.length) return true;
  if (!topic) return false;
  return filters.some((candidate) => {
    if (candidate.endsWith("*")) return topic.startsWith(candidate.slice(0, -1));
    return topic === candidate;
  });
}

function renderActivity(events) {
  if (!Array.isArray(events) || events.length === 0) {
    return emptyState("No events in this window.");
  }
  if (typeof renderBusEventRow !== "function") {
    return emptyState("Event formatters failed to load.");
  }
  const directions = typeof buildQuoteDirections === "function" ? buildQuoteDirections(events) : {};
  return events.map((event) => renderBusEventRow(event, directions)).join("");
}

function renderLiveFeed() {
  const node = document.getElementById("live-events-feed");
  if (node) node.innerHTML = renderActivity(liveEvents);
  const snapshot = document.getElementById("activity-feed");
  if (snapshot) {
    const compact = liveEvents.slice(0, Math.min(8, liveEvents.length));
    snapshot.innerHTML = renderActivity(compact);
  }
}

function humanTradingState(state) {
  const map = {
    trading_enabled: "Trading active",
    halted: "Trading halted",
    observing: "Observe only",
    broker_disconnected: "Broker offline",
  };
  return map[state] || String(state || "Unknown");
}

function humanBlocker(text) {
  const map = {
    "direct pattern intents disabled": "Pattern-based orders are turned off",
    "stock direct intents disabled (FX/metals only)": "Stock orders are turned off",
    "AI brain is HOLD": "AI advisor recommends waiting",
    "below_min_confidence": "Last setup did not meet the confidence threshold",
    "macro_gate": "News or macro risk blocked the last setup",
  };
  return map[text] || String(text || "Unknown blocker");
}

function renderOperatorStatus(summary) {
  const variant = summary.state === "trading_enabled" ? "live" : summary.state === "halted" ? "warn" : "safe";
  const blockers = Array.isArray(summary.blockers) && summary.blockers.length
    ? summary.blockers.map((item) => `<li>${asText(humanBlocker(item))}</li>`).join("")
    : "<li>No active blockers in the recent window.</li>";
  const ready = (summary.ready_symbols || []).join(", ") || "None";
  return `
    ${pillRow(pill(humanTradingState(summary.state), variant))}
    <p class="atom-muted">${asText(summary.headline)}</p>
    <div class="atom-label">Charts with live prices</div>
    <p>${asText(ready)}</p>
    <div class="atom-label">What is blocking trades</div>
    <ul>${blockers}</ul>
  `;
}

function renderRuntimeControls(controls) {
  const preset = String(controls.preset || "custom").replace(/_/g, " ");
  return `
    ${pillRow(
      controls.signal_direct_intents ? pill("Auto signals on", "live") : pill("Auto signals off", "safe"),
      controls.signal_macro_gate ? pill("News filter on", "safe") : pill("News filter off", "warn"),
      pill(preset, "safe"),
    )}
    ${kvHtml([
      ["Minimum confidence", controls.signal_min_confidence],
      ["News filter window (sec)", controls.signal_macro_gate_max_age_sec],
      ["AI mode", controls.llm_decision_mode],
      ["Last changed", controls.updated_ts ? new Date(controls.updated_ts * 1000).toLocaleString() : "Defaults"],
    ])}
    <div class="atom-label">Quick presets</div>
    <div class="organism-filter-row" id="preset-buttons">
      <button class="atom-button" data-preset="observe_only">Observe only</button>
      <button class="atom-button" data-preset="demo_cautious">Demo cautious</button>
      <button class="atom-button" data-preset="demo_aggressive">Demo aggressive</button>
      <button class="atom-button" data-preset="halted">Stop trading</button>
    </div>
  `;
}

function describeIntent(intent) {
  if (!intent) return "None";
  const reason = intent.reason || intent.blocked_reason || intent.message;
  return `${asText(intent.symbol)} ${asText(intent.side || intent.action)} · size ${asText(intent.qty)} · confidence ${formatPct(intent.confidence)}${reason ? ` · ${asText(reason)}` : ""}`;
}

function renderWhyTrade(status, signals, brain, orders) {
  return kvHtml([
    ["Summary", status.headline],
    ["Latest candidate", describeIntent(signals.latest_candidate)],
    ["Latest block", describeIntent(signals.latest_blocked)],
    ["AI action", brain.action || "Not available"],
    ["AI reasoning", brain.reasoning || "Not available"],
    ["Latest rejection", describeIntent(orders.latest_rejected)],
  ]);
}

function renderBrainSummary(brain) {
  if (!brain.available) return emptyState("The AI advisor has not run yet.");
  const llmLine = brain.llm_ok === false
    ? `<div class="organism-feed-item warn">${asText(brain.operator_message || brain.error_code || brain.error || "AI service unavailable")}</div>`
    : `<div class="organism-feed-item ok">${asText(brain.operator_message || "AI service is online")}</div>`;
  return llmLine + kvHtml([
    ["Recommendation", brain.action || "Not available"],
    ["Confidence", brain.confidence != null ? formatPct(brain.confidence) : "Not available"],
    ["Macro view", brain.macro_regime || "Not available"],
    ["Risk guard", brain.guard_ok ? "Passed" : asText(brain.guard_reason || "Blocked")],
    ["Model", `${asText(brain.provider)} / ${asText(brain.model)}`],
    ["Response time (ms)", brain.latency_ms],
    ["Reasoning", brain.reasoning || "Not available"],
  ]);
}

function renderSignalsSummary(signals) {
  return kvHtml([
    ["Last signal sent", describeIntent(signals.latest_emitted)],
    ["Last candidate", describeIntent(signals.latest_candidate)],
    ["Last block", describeIntent(signals.latest_blocked)],
    ["Block reasons (counts)", signals.block_counts || {}],
  ]);
}

function renderOrdersSummary(orders) {
  return kvHtml([
    ["Last intent", describeIntent(orders.latest_intent)],
    ["Last queued", describeIntent(orders.latest_queued)],
    ["Last sent", describeIntent(orders.latest_sent)],
    ["Last fill", describeIntent(orders.latest_filled)],
    ["Last rejection", describeIntent(orders.latest_rejected)],
  ]);
}

function formatLatencyLine(latency) {
  const parts = [];
  if (latency.intent_to_sent_sec != null && latency.intent_to_sent_sec !== undefined) {
    parts.push(`Intent→send ${Number(latency.intent_to_sent_sec).toFixed(2)}s`);
  }
  if (latency.sent_to_filled_sec != null && latency.sent_to_filled_sec !== undefined) {
    parts.push(`Send→fill ${Number(latency.sent_to_filled_sec).toFixed(2)}s`);
  }
  return parts.length ? parts.join(" · ") : "Still in progress";
}

function humanTradeState(state) {
  const map = {
    filled: "Filled",
    opened: "Open",
    closed: "Closed",
    reviewed: "Reviewed",
    rejected: "Rejected",
    blocked: "Blocked",
    vetted: "Risk cleared",
    timeout_unknown_broker_state: "Timed out",
    error: "Error",
    sent: "Sent",
    queued: "Queued",
    signal: "Signal only",
    intent: "Intent only",
  };
  return map[state] || String(state || "Unknown");
}

function tradeStateVariant(state) {
  if (["filled", "opened", "closed", "reviewed", "vetted"].includes(state)) return "safe";
  if (["rejected", "blocked", "error", "timeout_unknown_broker_state"].includes(state)) return "warn";
  return "live";
}

function humanStagePath(names) {
  const map = {
    signal: "Signal",
    intent: "Intent",
    immune_pass: "Risk OK",
    immune_block: "Risk block",
    queued: "Queued",
    sent: "Sent",
    filled: "Filled",
    rejected: "Rejected",
    position_opened: "Position open",
    position_closed: "Position closed",
    memory_opened: "Logged open",
    memory_closed: "Logged close",
    outcome: "Outcome",
    post_trade_review: "Reviewed",
  };
  return (names || []).map((n) => map[n] || n).join(" → ") || "—";
}

const DEFECT_LABELS = {
  fill_without_position_join: "Fill not joined to a position",
  missing_trade_outcome: "Closed without trade outcome",
  missing_post_trade_review: "Closed without post-trade review",
};

function reviewLine(review) {
  if (!review) return "";
  const matched = review.matched ? "matched on order_id" : `join: ${asText(review.join_status || "unmatched")}`;
  const verdict = review.verdict ? ` · ${asText(review.verdict)}` : "";
  return `<div class="atom-muted">Post-trade review (${matched})${verdict}</div>`;
}

function immuneLine(immune) {
  if (!immune) return "";
  if (immune.decision === "block") {
    const reasons = (immune.reasons || []).length ? (immune.reasons || []).join(", ") : "no reason given";
    return `<div class="atom-muted">Immune block: ${asText(reasons)}</div>`;
  }
  return `<div class="atom-muted">Immune: cleared</div>`;
}

function renderTradeLifecycle(lifecycle) {
  const trades = Array.isArray(lifecycle.trades) ? lifecycle.trades : [];
  if (trades.length === 0) {
    return emptyState("No order progress to show in this window.");
  }
  const items = trades.slice(0, 8).map((trade) => {
    const state = trade.state || "unknown";
    const variant = tradeStateVariant(state);
    const stages = humanStagePath(trade.stage_names);
    const timing = formatLatencyLine(trade.latency || {});
    const waiting = state === "vetted"
      ? "Passed risk checks — waiting for broker send."
      : state === "intent"
        ? "Intent recorded — risk review pending."
        : "";
    const defects = (trade.defects || []).map((d) => pill(DEFECT_LABELS[d] || d, "warn")).join("");
    return `
      <div class="organism-feed-item${trade.has_defect ? " is-blocked" : ""}">
        <div class="atom-row atom-row--spread">
          ${pill(humanTradeState(state), variant)}
          <span class="event-symbol">${asText(trade.symbol)}</span>
          ${trade.side ? `<span class="event-side event-side--${String(trade.side).toLowerCase() === "buy" ? "buy" : "sell"}">${asText(trade.side)}</span>` : ""}
          <span class="atom-muted">${trade.qty != null ? asText(trade.qty) : ""}</span>
        </div>
        <div class="atom-muted">${asText(stages)}</div>
        <div class="atom-muted">${asText(timing)}</div>
        ${immuneLine(trade.immune)}
        ${reviewLine(trade.review)}
        ${waiting ? `<div class="atom-muted">${waiting}</div>` : ""}
        ${trade.reason ? `<div class="atom-muted">${asText(trade.reason)}</div>` : ""}
        ${defects ? `<div class="atom-row">${defects}</div>` : ""}
      </div>
    `;
  }).join("");
  const defectBanner = lifecycle.defect_count
    ? `<p class="atom-muted">${pill(`${lifecycle.defect_count} join defect${lifecycle.defect_count === 1 ? "" : "s"}`, "warn")} flagged in this window.</p>`
    : "";
  return `${defectBanner}<div class="organism-feed organism-feed--compact">${items}</div>`;
}

async function applyPreset(preset) {
  const label = String(preset || "").replace(/_/g, " ");
  if (!window.confirm(`Switch trading mode to "${label}"?`)) return;
  const response = await fetch("/api/controls/preset", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({preset}),
  });
  if (!response.ok) throw new Error(`Could not apply preset (${response.status})`);
  await refresh({ silent: true });
}

function selectedTopicFilters() {
  return Array.from(document.querySelectorAll(".feed-topic-filter:checked")).map((node) => node.value);
}

function feedLimit() {
  const value = Number.parseInt(document.getElementById("feed-limit").value, 10);
  if (Number.isNaN(value) || value < 1) return 20;
  return value;
}

function stateUrl() {
  const params = new URLSearchParams();
  params.set("limit", String(feedLimit()));
  const topics = selectedTopicFilters();
  if (topics.length > 0) {
    params.set("topics", topics.join(","));
  }
  return `/api/state?${params.toString()}`;
}

function setStatus(message, variant) {
  const node = document.getElementById("dashboard-status");
  node.textContent = message;
  node.className = `atom-status atom-status--${variant}`;
}

async function fetchState() {
  const response = await fetch(stateUrl());
  if (!response.ok) {
    throw new Error(`Dashboard request failed (${response.status})`);
  }
  return response.json();
}

let hasLoadedOnce = false;
let liveEvents = [];
let silentRefreshTimer = null;
let lastStatusText = "";
let streamLive = false;
let streamPollOnly = false;
let lastPolledSeq = 0;

const PANEL_REFRESH_TOPIC_PATTERNS = [
  /^muscle\./,
  /^position\./,
  /^portfolio\./,
  /^memory\./,
  /^research\./,
  /^memory\.trade_(closed|opened)/,
  /^macro\.news\./,
  /^cortex\.(decision|brain\.result|decision_guard)/,
  /^rd\.(promotion\.|dream\.cycle\.)/,
  /^ops\.control\./,
  /^market\.signal/,
  /^immune\.(pass|block|position\.)/,
  /^swarm\.research\.complete/,
  /^introspect\.(score_update|post_trade_review)/,
];

function upsertLiveEvent(event) {
  if (!topicMatches(event.topic, selectedTopicFilters())) return;
  const seq = event.seq;
  liveEvents = [event, ...liveEvents.filter((row) => row.seq !== seq)];
  const limit = feedLimit();
  if (liveEvents.length > limit) liveEvents = liveEvents.slice(0, limit);
  renderLiveFeed();
}

function seedLiveEvents(events) {
  liveEvents = (events || [])
    .filter((event) => topicMatches(event.topic, selectedTopicFilters()))
    .slice(0, feedLimit());
  renderLiveFeed();
}

function setLiveEventsStatus(label, variant) {
  const node = document.getElementById("live-events-status");
  if (!node) return;
  node.textContent = label;
  node.className = `atom-pill atom-pill--${variant || "safe"}`;
}

async function pollRecentEvents() {
  const params = new URLSearchParams();
  params.set("since_seq", String(Math.max(0, lastPolledSeq, TradingDeskEventBus?.lastSeq || 0)));
  params.set("limit", String(Math.max(feedLimit(), 40)));
  const topics = selectedTopicFilters();
  if (topics.length) params.set("topics", topics.join(","));
  try {
    const response = await fetch(`/api/events/recent?${params.toString()}`, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    const rows = Array.isArray(payload.events) ? payload.events : [];
    for (const event of rows) {
      if (event.seq != null) lastPolledSeq = Math.max(lastPolledSeq, Number(event.seq) || 0);
      upsertLiveEvent(event);
    }
  } catch (_err) {
    /* polling fallback is best-effort */
  }
}

function mergePolledEvents(events) {
  if (!Array.isArray(events)) return;
  for (const event of events) {
    if (event.seq != null) lastPolledSeq = Math.max(lastPolledSeq, Number(event.seq) || 0);
    upsertLiveEvent(event);
  }
}

function scheduleSilentRefresh() {
  if (silentRefreshTimer) clearTimeout(silentRefreshTimer);
  silentRefreshTimer = setTimeout(() => refresh({ silent: true }), 2500);
}

function shouldRefreshPanelsForTopic(topic) {
  const value = String(topic || "");
  return PANEL_REFRESH_TOPIC_PATTERNS.some((pattern) => pattern.test(value));
}

function mountPortfolioSparkline(panel) {
  if (!window.EquitySparkline || !panel?.equity_curve?.available) return;
  const points = panel.equity_curve.points || [];
  if (!points.length) return;
  EquitySparkline.mount(points, { message: panel.message || "" });
}

function connectEventStream() {
  if (!window.TradingDeskEventBus) return;
  TradingDeskEventBus.connectWithFilters({
    sinceSeq: TradingDeskEventBus.lastSeq || 0,
    topics: selectedTopicFilters(),
  });
}

function initEventBus() {
  if (!window.TradingDeskEventBus) return;
  TradingDeskEventBus.on("connected", () => {
    streamLive = true;
    streamPollOnly = false;
    setLiveEventsStatus("Live", "safe");
  });
  TradingDeskEventBus.on("disconnected", (meta) => {
    streamLive = false;
    const attempts = meta?.attempts || 0;
    if (attempts >= 5) {
      streamPollOnly = true;
      setLiveEventsStatus("Polling", "warn");
      pollRecentEvents();
    } else {
      setLiveEventsStatus("Reconnecting", "warn");
    }
  });
  TradingDeskEventBus.on("unsupported", () => {
    streamLive = false;
    streamPollOnly = true;
    setLiveEventsStatus("Polling", "warn");
    pollRecentEvents();
  });
  TradingDeskEventBus.on("bus.event", (event) => {
    upsertLiveEvent(event);
    if (shouldRefreshPanelsForTopic(event.topic)) scheduleSilentRefresh();
  });
  TradingDeskEventBus.on("topic:portfolio.equity", (event) => {
    if (window.EquitySparkline && event?.payload) {
      EquitySparkline.appendLivePoint(event.payload);
    }
  });
  TradingDeskEventBus.connectWithFilters({
    sinceSeq: TradingDeskEventBus.lastSeq || 0,
    topics: selectedTopicFilters(),
  });
}

async function refresh(options = {}) {
  const silent = options.silent === true;
  if (!hasLoadedOnce && !silent) {
    setStatus("Loading your desk...", "loading");
  }

  try {
    const state = await fetchState();
    const bridge = state.bridge_status || {};
    const statusSummary = state.status_summary || {};
    const runtimeControls = state.runtime_controls || {};
    const brain = state.brain_summary || {};
    const signals = state.signals_summary || {};
    const orders = state.orders_summary || {};
    const lifecycle = state.trade_lifecycle || {};
    const trader = state.trader_panels || {};
    const loadedCount = Array.isArray(state.recent_events) ? state.recent_events.length : 0;

    document.getElementById("research-watchlist").innerHTML = renderResearchWatchlist(trader.research_watchlist);
    if (window.EquitySparkline) EquitySparkline.destroy();
    document.getElementById("portfolio-pnl-panel").innerHTML = renderPortfolioPnlPanel(trader.portfolio_pnl);
    mountPortfolioSparkline(trader.portfolio_pnl);
    document.getElementById("positions-panel").innerHTML = renderPositionsPanel(trader.positions);
    document.getElementById("macro-news-panel").innerHTML = renderMacroNewsPanel(trader.macro_news);
    document.getElementById("forecast-thesis-panel").innerHTML = renderForecastThesisPanel(trader.forecast_thesis);
    document.getElementById("edge-validation-panel").innerHTML = renderEdgeValidationPanel(trader.edge_validation);
    document.getElementById("signal-drilldown").innerHTML = renderSignalDrilldown(trader.signal_drilldown);
    document.getElementById("readiness-panel").innerHTML = renderReadinessPanel(trader.readiness);
    document.getElementById("pending-promotions").innerHTML = renderPendingPromotions(trader.pending_promotions);
    document.getElementById("dream-lab-status").innerHTML = renderDreamLabStatus(trader.dream_lab);
    document.getElementById("operator-status").innerHTML = renderOperatorStatus(statusSummary);
    document.getElementById("runtime-controls").innerHTML = renderRuntimeControls(runtimeControls);
    document.getElementById("system-health").innerHTML = renderSystemHealth(state);
    document.getElementById("telemetry-summary").innerHTML = renderTelemetry(state.telemetry_summary || {});
    document.getElementById("bridge-status").innerHTML = renderBridgeStatus(bridge);
    document.getElementById("safety-flags").innerHTML = renderSafetyFlags(state.safety_flags || {});
    document.getElementById("why-trade").innerHTML = renderWhyTrade(statusSummary, signals, brain, orders);
    document.getElementById("brain-summary").innerHTML = renderBrainSummary(brain);
    document.getElementById("signals-summary").innerHTML = renderSignalsSummary(signals);
    document.getElementById("orders-summary").innerHTML = renderOrdersSummary(orders);
    document.getElementById("trade-lifecycle").innerHTML = renderTradeLifecycle(lifecycle);

    if (!hasLoadedOnce) {
      seedLiveEvents(state.recent_events || []);
    } else if (streamPollOnly || !streamLive) {
      mergePolledEvents(state.recent_events || []);
    }

    if (streamPollOnly) {
      pollRecentEvents();
    }

    const nextStatus = formatStatusLine(new Date().toLocaleTimeString(), trader, statusSummary, loadedCount);
    if (nextStatus !== lastStatusText) {
      setStatus(nextStatus, "ok");
      lastStatusText = nextStatus;
    }
    hasLoadedOnce = true;
  } catch (error) {
    if (!hasLoadedOnce) {
      for (const id of [
        "research-watchlist", "portfolio-pnl-panel", "positions-panel", "macro-news-panel",
        "forecast-thesis-panel", "edge-validation-panel", "signal-drilldown", "readiness-panel",
        "pending-promotions", "dream-lab-status", "live-events-feed",
        "system-health", "operator-status", "runtime-controls", "telemetry-summary", "bridge-status",
        "safety-flags", "why-trade", "brain-summary", "signals-summary", "orders-summary", "trade-lifecycle", "activity-feed",
      ]) {
        const node = document.getElementById(id);
        if (node) node.innerHTML = emptyState("Could not reach the dashboard service.");
      }
      setStatus(`Could not load dashboard: ${asText(error.message)}`, "error");
    }
  }
}

function onFeedFiltersChanged() {
  liveEvents = liveEvents.filter((event) => topicMatches(event.topic, selectedTopicFilters()));
  renderLiveFeed();
  connectEventStream();
  refresh({ silent: true });
}

document.getElementById("feed-limit").addEventListener("change", () => {
  liveEvents = liveEvents.slice(0, feedLimit());
  renderLiveFeed();
});

for (const checkbox of document.querySelectorAll(".feed-topic-filter")) {
  checkbox.addEventListener("change", () => {
    onFeedFiltersChanged();
  });
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-preset]");
  if (button) {
    applyPreset(button.dataset.preset).catch((error) => setStatus(asText(error.message), "error"));
    return;
  }

  const promoButton = event.target.closest("[data-promotion-action]");
  if (!promoButton) return;
  const action = promoButton.dataset.promotionAction;
  const promoId = promoButton.dataset.promoId;
  if (!action || !promoId) return;
  promoButton.disabled = true;
  promotionAction(action, promoId)
    .then(() => refresh({ silent: true }))
    .catch((error) => setStatus(asText(error.message), "error"))
    .finally(() => {
      promoButton.disabled = false;
    });
});

initEventBus();
refresh();
setInterval(() => refresh({ silent: true }), 10000);
