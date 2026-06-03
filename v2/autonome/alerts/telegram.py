"""
autonome/alerts/telegram.py  v2.0
Telegram alert sender for critical trading events.
Optional — silently no-ops if alerts.telegram.enabled == false or credentials missing.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests
import yaml

log = logging.getLogger("alerts.telegram")


class TelegramAlertSender:
    """
    Sends Telegram messages for critical trading events.
    Reads config from settings.yaml (enabled flag) and secrets.yaml (bot_token, chat_id).
    """

    TELEGRAM_API_FMT = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        """
        If bot_token/chat_id are not provided, reads from config/secrets.yaml.
        Also reads enabled flag from config/settings.yaml.
        """
        self._enabled = False
        self._token: str | None = None
        self._chat_id: str | None = None

        # Load settings to check enabled flag
        try:
            settings_path = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
            with open(settings_path) as f:
                settings = yaml.safe_load(f)
            alert_cfg = settings.get("alerts", {}).get("telegram", {})
            self._enabled = bool(alert_cfg.get("enabled", False))
        except (OSError, yaml.YAMLError):
            self._enabled = False

        if not self._enabled:
            return

        # Use explicit credentials if passed
        if bot_token and chat_id:
            self._token = bot_token
            self._chat_id = str(chat_id)
            return

        # Otherwise load from secrets
        try:
            secrets_path = os.path.join(os.path.dirname(__file__), "../../config/secrets.yaml")
            with open(secrets_path) as f:
                secrets = yaml.safe_load(f)
            sec = secrets.get("telegram", {})
            self._token = (sec.get("bot_token", "") or "").strip()
            self._chat_id = (sec.get("chat_id", "") or "").strip()
            if not self._token or not self._chat_id:
                log.warning("Telegram enabled but bot_token/chat_id missing in secrets.yaml — disabling")
                self._enabled = False
        except (OSError, yaml.YAMLError):
            log.warning("Failed to read telegram secrets — disabling alerts")
            self._enabled = False

    # ── core send ───────────────────────────────────────────────────────────

    def send(self, message: str) -> bool:
        """Send a plain text message. Returns True if sent successfully."""
        if not self._enabled or not self._token or not self._chat_id:
            return False
        url = self.TELEGRAM_API_FMT.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": False,
        }
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.error("Telegram send failed: %s", exc)
            return False

    def send_alert(self, title: str, body: str) -> bool:
        """Send a formatted alert with title and body."""
        emoji = {
            "HALT": "🛑",
            "REJECTED": "🚫",
            "LIMIT": "⚠️",
            "ENTERED": "🔵",
            "EXITED": "✅",
            "ERROR": "🔴",
            "WARNING": "⚠️",
        }
        # Pick an emoji based on title keywords
        icon = "📢"
        upper = title.upper()
        for key, val in emoji.items():
            if key in upper:
                icon = val
                break
        msg = f"{icon} <b>{title}</b>\n\n{body}"
        return self.send(msg)

    # ── position lifecycle ──────────────────────────────────────────────────

    def send_position_entered(self, trade) -> bool:
        """
        Alert when a position is successfully entered.
        `trade` is expected to have attributes: symbol, side, qty, entry_price.
        """
        entry_price = getattr(trade, "entry_price", None) or getattr(trade, "price", 0)
        body = (
            f"Symbol:  {trade.symbol}\n"
            f"Side:    {trade.side.upper()}\n"
            f"Qty:     {trade.qty:.4f}\n"
            f"Entry:   {entry_price:.2f}" if entry_price else f"Entry:   pending"
        )
        return self.send_alert("POSITION ENTERED", body)

    def send_position_exited(self, trade, pnl: float | None = None) -> bool:
        """
        Alert when a position is exited.
        `trade` should have attributes: symbol, side, qty, entry_price.
        `pnl` is optional realized P&L.
        """
        entry_price = getattr(trade, "entry_price", None) or getattr(trade, "price", 0)
        pnl_str = f"P&L:     {pnl:+.2f}\n" if pnl is not None else ""
        body = (
            f"Symbol:  {trade.symbol}\n"
            f"Side:    {trade.side.upper()}\n"
            f"Qty:     {trade.qty:.4f}\n"
            f"Entry:   {entry_price:.2f}\n"
            f"{pnl_str}"
        )
        return self.send_alert("POSITION EXITED", body)

    # ── specific halt / rejection alerts ───────────────────────────────────

    def send_order_rejected(self, trade, error: str | None = None) -> bool:
        """Alert when an order is rejected."""
        body = f"Symbol:  {getattr(trade, 'symbol', 'UNKNOWN')}\n"
        if hasattr(trade, 'side'):
            body += f"Side:    {trade.side.upper()}\n"
        if hasattr(trade, 'qty'):
            body += f"Qty:     {trade.qty:.4f}\n"
        body += f"Error:   {error or getattr(trade, 'error', 'Unknown')}"
        return self.send_alert("ORDER REJECTED", body)

    def send_drawdown_halt(self, drawdown_pct: float, equity: float) -> bool:
        """Alert when drawdown halt is triggered."""
        body = f"Drawdown: {drawdown_pct:.2%}\n" f"Equity:   {equity:.2f}\n\nManual resume required."
        return self.send_alert("DRAWDOWN HALT", body)

    def send_api_halt(self, failure_count: int, context: str) -> bool:
        """Alert when API failure halt is triggered."""
        body = f"Consecutive failures: {failure_count}\nContext: {context}\n\nManual resume required."
        return self.send_alert("API HALT", body)

    def send_daily_loss_halt(self, loss: float, equity: float) -> bool:
        """Alert when daily loss limit is reached."""
        body = f"Daily loss: {loss:.2f}\n" f"Equity:     {equity:.2f}\n\nManual resume required."
        return self.send_alert("DAILY LOSS LIMIT", body)

    def send_volatility_halt(self, vol: float) -> bool:
        """Alert when volatility pause is triggered."""
        body = f"Annual realized vol: {vol:.1%}\n\nNew signals paused until vol subsides."
        return self.send_alert("VOLATILITY HALT", body)
