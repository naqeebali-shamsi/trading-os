#!/usr/bin/env python3
"""
scripts/india_dashboard_server.py  v1.1
India Trading Dashboard Server — fast file-based serving.

Serves the India manual trading dashboard at http://localhost:8766
Uses pre-generated JSON files for instant response.

Usage:
    python3 scripts/india_dashboard_server.py
"""
import sys, os, json, logging
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
log = logging.getLogger("india.dashboard")

PORT = 8766
INTEL_DIR = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel"
DASHBOARD_HTML = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/dashboard/india.html"


def load_signals():
    """Load pre-generated signals from JSON file."""
    path = os.path.join(INTEL_DIR, "india_signals.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        log.warning("No signals file found: %s", e)
        return {"generated_at": None, "count": 0, "signals": []}


def load_macro():
    """Load macro snapshot."""
    path = os.path.join(INTEL_DIR, "india_macro.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        log.warning("No macro file found: %s", e)
        return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/india":
            self._serve_html()
        elif path == "/api/india_signals":
            self._serve_signals()
        elif path == "/api/india_macro":
            self._serve_macro()
        else:
            self.send_error(404)

    def _serve_html(self):
        try:
            with open(DASHBOARD_HTML, "r") as f:
                html = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html.encode())
        except Exception as e:
            log.error("HTML serve error: %s", e)
            self.send_error(500)

    def _serve_signals(self):
        try:
            data = load_signals()
            macro = load_macro()
            response = {
                "generated_at": data.get("generated_at"),
                "count": data.get("count", 0),
                "regime": macro.get("recommended_regime", "UNKNOWN"),
                "macro": {
                    "usd_inr": macro.get("usd_inr"),
                    "oil_usd": macro.get("oil_usd"),
                    "risk_score": macro.get("risk_score"),
                    "regime": macro.get("recommended_regime"),
                    "thesis": macro.get("thesis"),
                },
                "signals": data.get("signals", []),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(response, indent=2, default=str).encode())
        except Exception as e:
            log.error("Signals serve error: %s", e)
            self.send_error(500)

    def _serve_macro(self):
        try:
            data = load_macro()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2, default=str).encode())
        except Exception as e:
            self.send_error(500)


def run():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log.info("=" * 50)
    log.info("India Trading Dashboard")
    log.info("URL: http://localhost:%d", PORT)
    log.info("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    run()
