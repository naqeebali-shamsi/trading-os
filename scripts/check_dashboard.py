#!/usr/bin/env python3
import json
import urllib.request

BASE = "http://127.0.0.1:8765"
results = {}

try:
    r = urllib.request.urlopen(f"{BASE}/api/state?limit=10", timeout=10)
    d = json.loads(r.read())
    results["state_ok"] = True
    results["event_count"] = len(d.get("recent_events", []))
    tp = d.get("trader_panels") or {}
    results["dream_lab_available"] = (tp.get("dream_lab") or {}).get("available")
    results["dream_lab_message"] = (tp.get("dream_lab") or {}).get("message")
    results["promotions_available"] = (tp.get("pending_promotions") or {}).get("available")
    results["portfolio_available"] = (tp.get("portfolio_pnl") or {}).get("available")
    results["portfolio_message"] = (tp.get("portfolio_pnl") or {}).get("message")
    lc = d.get("trade_lifecycle") or {}
    results["lifecycle_trades"] = len(lc.get("trades") or [])
    results["lifecycle_counts"] = lc.get("counts")
    if lc.get("trades"):
        t = lc["trades"][0]
        results["latest_trade"] = {
            k: t.get(k)
            for k in ["order_id", "symbol", "side", "qty", "state", "stage_names", "latency", "reason"]
        }
    orders = d.get("orders_summary") or {}
    results["orders"] = {
        "latest_intent": orders.get("latest_intent"),
        "latest_sent": orders.get("latest_sent"),
        "latest_filled": orders.get("latest_filled"),
        "latest_rejected": orders.get("latest_rejected"),
    }
    signals = d.get("signals_summary") or {}
    results["signals"] = {
        "latest_emitted": signals.get("latest_emitted"),
        "latest_blocked": signals.get("latest_blocked"),
    }
except Exception as exc:
    results["state_ok"] = False
    results["state_error"] = str(exc)

try:
    r = urllib.request.urlopen(f"{BASE}/api/events/health", timeout=3)
    results["sse_health"] = json.loads(r.read())
except Exception as exc:
    results["sse_health"] = {"error": str(exc)}

for path in ["/static/events.css", "/static/event_formatters.js"]:
    try:
        r = urllib.request.urlopen(BASE + path, timeout=3)
        body = r.read(400).decode("utf-8", "replace")
        results[path] = {"ok": True, "has_design": "event-symbol" in body or "renderBusEventRow" in body}
    except Exception as exc:
        results[path] = {"ok": False, "error": str(exc)}

try:
    r = urllib.request.urlopen(f"{BASE}/api/events/recent?limit=12", timeout=5)
    ev = json.loads(r.read())
    rows = ev.get("events") or []
    results["recent_topics"] = [x.get("topic") for x in rows]
    results["has_immune_anomaly"] = any(x.get("topic") == "immune.anomaly" for x in rows)
    results["has_tick"] = any(str(x.get("topic", "")).startswith("market.tick") for x in rows)
except Exception as exc:
    results["recent_error"] = str(exc)

print(json.dumps(results, indent=2, default=str))
