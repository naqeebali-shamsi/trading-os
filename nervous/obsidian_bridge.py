#!/usr/bin/env python3
"""nervous/obsidian_bridge.py -- Shared Obsidian write interface.

Any layer can import and call append_note(section, content) or
write_daily(content). Uses Obsidian-compatible markdown + wikilinks.

VAULT root defaults to ``<workspace>/vault``; override with ``TRADING_OS_VAULT``.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import sys

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from paths import vault_dir  # noqa: E402

VAULT = vault_dir(create=True)

FOLDERS = {
    "daily":     VAULT / "01-Daily",
    "trades":    VAULT / "02-Trades",
    "market":    VAULT / "03-Market",
    "strategies":VAULT / "04-Strategies",
    "immune":    VAULT / "05-Immune",
    "system":    VAULT / "06-System",
    "reviews":   VAULT / "07-Reviews",
}

for f in FOLDERS.values():
    f.mkdir(parents=True, exist_ok=True)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _daily_path(section: str) -> Path:
    name = f"{_date()}.md"
    return FOLDERS[section] / name


def append_note(section: str, heading: str, body: str, tags: list = None):
    """Append a heading+body block to today's note in the given section.

    section: daily | trades | market | strategies | immune | system | reviews
    """
    path = _daily_path(section)
    tags_str = " " + " ".join(f"#{t}" for t in tags) if tags else ""
    block = f"\n## {_ts()}{tags_str}\n\n{body}\n"

    if path.exists():
        path.write_text(path.read_text() + block)
    else:
        header = f"# {heading} — {_date()}\n\n"
        path.write_text(header + block)

    return str(path)


def write_trade(trade: dict):
    """Write a structured trade entry to 02-Trades/YYYY-MM-DD.md"""
    tid = trade.get("order_id", "unknown")
    body = (
        f"| field | value |\n"
        f"|-------|-------|\n"
        f"| order_id | `{tid}` |\n"
        f"| symbol | {trade.get('symbol','')} |\n"
        f"| side | {trade.get('side','')} |\n"
        f"| qty | {trade.get('qty','')} |\n"
        f"| fill_price | {trade.get('fill_price','')} |\n"
        f"| tp | {trade.get('tp','')} |\n"
        f"| sl | {trade.get('sl','')} |\n"
        f"| status | {trade.get('status','')} |\n"
        f"\n[[Strategy Registry]]\n"
    )
    return append_note("trades", f"Trade {tid}", body, tags=["trade", trade.get("symbol","")])


def write_alert(alert: dict):
    """Write an alert / block event to 05-Immune/YYYY-MM-DD.md"""
    level = alert.get("level", "INFO")
    source = alert.get("source", "system")
    msg = alert.get("msg", json.dumps(alert))
    body = f"**{level}** — `{source}`\n\n> {msg}\n"
    return append_note("immune", f"Risk Event — {level}", body, tags=["risk", level.lower(), source])


def write_market_snapshot(tick: dict):
    """Write a market tick snapshot to 03-Market/YYYY-MM-DD.md"""
    sym = tick.get("symbol", "UNKNOWN")
    body = f"- `{sym}` bid={tick.get('bid')} ask={tick.get('ask')}"
    append_note("market", f"Market Snapshot — {sym}", body, tags=["tick", sym])


def write_daily_review(stats: dict):
    """Generate the end-of-day review note in 07-Reviews/YYYY-MM-DD.md"""
    date = _date()
    body = (
        f"## Performance\n\n"
        f"- PnL: {stats.get('pnl', 'N/A')}\n"
        f"- Win rate: {stats.get('win_rate', 'N/A')}\n"
        f"- Sharpe: {stats.get('sharpe', 'N/A')}\n"
        f"- Trades: {stats.get('trade_count', 'N/A')}\n\n"
        f"## Decisions\n\n"
        f"{stats.get('decisions', '*No decisions logged.*')}\n\n"
        f"## Anomalies\n\n"
        f"{stats.get('anomalies', '*No anomalies.*')}\n\n"
        f"## Next Day\n\n"
        f"{stats.get('next', '*TBD*')}\n"
    )
    path = FOLDERS["reviews"] / f"{date}.md"
    path.write_text(f"# EOD Review — {date}\n\n{body}")
    return str(path)


def write_system_event(msg: str, level="INFO"):
    body = f"`{level}`: {msg}"
    return append_note("system", "System Event", body, tags=["sys", level.lower()])
