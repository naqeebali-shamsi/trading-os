/**
 * Event feed design system — minimal rows, symbol pills, price direction.
 */
(function initEventFormatters(global) {
  const REASON = {
    below_min_confidence: "Confidence too low",
    macro_gate: "Macro filter",
    no_fresh_tick: "Stale quote",
    signal_emitted: "Ready to trade",
    volatility_spike: "Volatility spike",
  };

  function payloadOf(event) {
    const raw = event?.payload;
    if (raw && typeof raw === "object") return raw;
    if (typeof raw === "string") {
      try {
        return JSON.parse(raw);
      } catch (_err) {
        return { message: raw };
      }
    }
    return {};
  }

  function topicOf(event) {
    return String(event?.topic || "");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatPrice(value) {
    if (value === null || value === undefined) return "—";
    const n = Number(value);
    if (Number.isNaN(n)) return String(value);
    if (Math.abs(n) >= 100) return n.toFixed(2);
    if (Math.abs(n) >= 1) return n.toFixed(4);
    return n.toFixed(5);
  }

  function formatMoney(value) {
    if (value === null || value === undefined) return "—";
    const n = Number(value);
    if (Number.isNaN(n)) return String(value);
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function formatPct(value) {
    if (value === null || value === undefined) return "";
    const n = Number(value);
    if (Number.isNaN(n)) return "";
    const pct = n <= 1 ? n * 100 : n;
    return `${pct.toFixed(0)}%`;
  }

  function humanReason(reason) {
    const key = String(reason || "").trim();
    return REASON[key] || key.replace(/_/g, " ") || "";
  }

  function symbolFromTopic(topic, payload) {
    if (payload?.symbol) return String(payload.symbol);
    const tick = topic.match(/^market\.tick\.(.+)$/);
    if (tick) return tick[1];
    const candle = topic.match(/^candle\.close\.(.+)$/);
    if (candle) return candle[1];
    return "";
  }

  function isTickEvent(event) {
    return topicOf(event).startsWith("market.tick");
  }

  function midPrice(payload) {
    const bid = Number(payload?.bid);
    const ask = Number(payload?.ask);
    if (!Number.isNaN(bid) && !Number.isNaN(ask)) return (bid + ask) / 2;
    if (!Number.isNaN(bid)) return bid;
    if (!Number.isNaN(ask)) return ask;
    if (payload?.last_price != null) return Number(payload.last_price);
    if (payload?.close != null) return Number(payload.close);
    if (payload?.price != null) return Number(payload.price);
    return null;
  }

  /** Precompute up/down/flat per seq (events processed oldest → newest). */
  function buildQuoteDirections(events) {
    const sorted = [...events].sort((a, b) => (Number(a.seq) || 0) - (Number(b.seq) || 0));
    const lastMid = {};
    const directions = {};
    for (const event of sorted) {
      if (!isTickEvent(event)) continue;
      const p = payloadOf(event);
      const sym = symbolFromTopic(topicOf(event), p);
      const mid = midPrice(p);
      if (!sym || mid == null || Number.isNaN(mid)) continue;
      const prev = lastMid[sym];
      if (prev != null) {
        directions[event.seq] = mid > prev ? "up" : mid < prev ? "down" : "flat";
      } else {
        directions[event.seq] = "flat";
      }
      lastMid[sym] = mid;
    }
    return directions;
  }

  function rowHtml({ symbol, value, valueClass, badge, badgeClass, side, note, time }) {
    const sym = symbol ? `<span class="event-symbol">${escapeHtml(symbol)}</span>` : "";
    const val = value != null && value !== ""
      ? `<span class="event-value ${valueClass || ""}">${escapeHtml(value)}</span>`
      : "";
    const badgeHtml = badge
      ? `<span class="event-badge ${badgeClass || ""}">${escapeHtml(badge)}</span>`
      : "";
    const sideHtml = side ? `<span class="event-side ${side.class || ""}">${escapeHtml(side.text)}</span>` : "";
    const noteHtml = note ? `<div class="event-row__note">${escapeHtml(note)}</div>` : "";
    return `
      <div class="event-row">
        <div class="event-row__main">${sym}${badgeHtml}${sideHtml}${val}</div>
        <span class="event-row__time">${escapeHtml(time)}</span>
        ${noteHtml}
      </div>`;
  }

  function renderQuoteRow(event, directions) {
    const p = payloadOf(event);
    const sym = symbolFromTopic(topicOf(event), p) || "—";
    const mid = midPrice(p);
    const dir = directions[event.seq] || "flat";
    const valueClass = dir === "up" ? "event-value--up" : dir === "down" ? "event-value--down" : "event-value--flat";
    const time = event.ts ? new Date(event.ts * 1000).toLocaleTimeString() : "";
    return rowHtml({
      symbol: sym,
      value: mid != null ? formatPrice(mid) : "—",
      valueClass,
      time,
    });
  }

  function sideMeta(side) {
    const s = String(side || "").toUpperCase();
    if (s === "BUY") return { text: "Buy", class: "event-side--buy" };
    if (s === "SELL") return { text: "Sell", class: "event-side--sell" };
    return null;
  }

  function renderBusEventRow(event, directions) {
    const topic = topicOf(event);
    const p = payloadOf(event);
    const time = event.ts ? new Date(event.ts * 1000).toLocaleTimeString() : "";

    if (isTickEvent(event)) return renderQuoteRow(event, directions);

    if (topic === "market.regime") {
      const sym = p.symbol || "";
      return rowHtml({
        symbol: sym || null,
        badge: String(p.regime || "Regime"),
        note: p.last_price != null ? `Last ${formatPrice(p.last_price)}` : "",
        time,
      });
    }

    if (topic.startsWith("market.signal")) {
      const blocked = topic.includes("blocked");
      const sym = p.symbol || symbolFromTopic(topic, p);
      return rowHtml({
        symbol: sym,
        badge: blocked ? "Blocked" : topic.includes("candidate") ? "Setup" : "Signal",
        badgeClass: blocked ? "event-badge--warn" : "event-badge--safe",
        side: sideMeta(p.side),
        note: humanReason(p.reason || p.blocked_reason) || (formatPct(p.confidence) ? `${formatPct(p.confidence)} confidence` : ""),
        time,
      });
    }

    if (topic.startsWith("muscle.order.")) {
      const sym = p.symbol || "—";
      const map = {
        "muscle.order.filled": ["Filled", "event-badge--safe"],
        "muscle.order.rejected": ["Rejected", "event-badge--live"],
        "muscle.order.sent": ["Sent", ""],
        "muscle.order.intent": ["Intent", ""],
        "muscle.order.queued": ["Queued", ""],
        "muscle.order.timeout": ["Timeout", "event-badge--warn"],
        "muscle.order.error": ["Error", "event-badge--live"],
      };
      const [badge, badgeClass] = map[topic] || ["Order", ""];
      return rowHtml({
        symbol: sym,
        badge,
        badgeClass,
        side: sideMeta(p.side),
        note: p.qty != null ? `Size ${p.qty}` : "",
        time,
      });
    }

    if (topic === "immune.anomaly") {
      const label = humanReason(p.type) || "Unusual move";
      const price = p.price != null ? formatPrice(p.price) : "";
      const z = p.z != null ? `Z ${Number(p.z).toFixed(1)}` : "";
      return rowHtml({
        badge: "Alert",
        badgeClass: "event-badge--warn",
        value: price,
        valueClass: "event-value--flat",
        note: [label, z].filter(Boolean).join(" · "),
        time,
      });
    }

    if (topic.startsWith("immune.")) {
      const blocked = topic.includes("block");
      return rowHtml({
        symbol: p.intent?.symbol || p.symbol,
        badge: blocked ? "Risk block" : "Risk OK",
        badgeClass: blocked ? "event-badge--live" : "event-badge--safe",
        note: blocked ? humanReason((p.reasons || [])[0]) : "",
        time,
      });
    }

    if (topic === "portfolio.equity") {
      return rowHtml({
        badge: "Account",
        badgeClass: "event-badge--safe",
        value: p.equity != null ? `$${formatMoney(p.equity)}` : "",
        note: p.floating_pnl != null ? `Floating ${formatMoney(p.floating_pnl)}` : "",
        time,
      });
    }

    if (topic.startsWith("position.")) {
      return rowHtml({
        symbol: p.symbol,
        badge: topic === "position.opened" ? "Opened" : topic === "position.closed" ? "Closed" : "Position",
        value: p.profit != null ? formatMoney(p.profit) : "",
        valueClass: Number(p.profit) >= 0 ? "event-value--up" : "event-value--down",
        time,
      });
    }

    if (topic.startsWith("cortex.decision")) {
      return rowHtml({
        badge: String(p.action || p.recommendation || "AI"),
        note: p.assessment || (p.reasoning ? String(p.reasoning).slice(0, 100) : ""),
        time,
      });
    }

    if (topic.startsWith("rd.")) {
      if (topic === "rd.dream.cycle.complete" || topic === "rd.dream.cycle.start") {
        return rowHtml({
          badge: "Dream Lab",
          note: `${p.cycle || "cycle"} ${topic.endsWith("complete") ? "finished" : "started"}`,
          time,
        });
      }
      if (topic === "rd.promotion.proposed") {
        return rowHtml({
          badge: "Proposal",
          badgeClass: "event-badge--warn",
          note: String(p.type || "").replace(/_/g, " "),
          time,
        });
      }
      return rowHtml({ badge: "Dream Lab", note: topic.replace(/^rd\./, ""), time });
    }

    if (topic.startsWith("research.")) {
      return rowHtml({
        badge: "Research",
        badgeClass: "event-badge--safe",
        note: p.ranked_count != null ? `${p.ranked_count} names ranked` : "Scan complete",
        time,
      });
    }

    if (topic.startsWith("memory.learner")) {
      return rowHtml({
        badge: "Learning",
        note: [p.strategy_id, p.action].filter(Boolean).join(" · "),
        time,
      });
    }

    if (topic.startsWith("macro.news.")) {
      return rowHtml({
        badge: "News",
        note: p.title || p.route || "",
        time,
      });
    }

    return rowHtml({
      badge: topic.split(".").slice(-1)[0] || "Event",
      note: humanReason(p.reason) || p.message || p.summary || "",
      time,
    });
  }

  global.buildQuoteDirections = buildQuoteDirections;
  global.renderBusEventRow = renderBusEventRow;
})(window);
