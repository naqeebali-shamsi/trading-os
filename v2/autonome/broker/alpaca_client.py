"""
autonome/broker/alpaca_client.py  v2.0
Unified Alpaca REST client with paper/live gate.
"""
from __future__ import annotations

import os, time, logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
import yaml

log = logging.getLogger("alpaca")


@dataclass(frozen=True)
class Account:
    equity: float
    buying_power: float
    cash: float
    daytrade_count: int
    status: str  # ACTIVE, etc.
    margin_enabled: bool = False


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float


@dataclass(frozen=True)
class OrderResult:
    id: str
    symbol: str
    side: str   # buy / sell
    qty: float
    filled_avg_price: Optional[float]
    status: str  # new / partially_filled / filled / canceled / expired
    filled_qty: Optional[float] = None
    error: Optional[str] = None


class AlpacaClient:
    """
    Thread-safe-ish (requests session per instance).
    Deliberate mode gate: constructor validates mode==PAPER unless explicitly overridden.
    """
    def __init__(self, mode: str = "PAPER"):
        self.mode = mode.upper()
        if self.mode not in ("PAPER", "LIVE"):
            raise ValueError(f"Mode must be PAPER or LIVE, got {mode}")

        # LIVE mode safety gate
        if self.mode == "LIVE":
            if os.environ.get("AUTONOME_LIVE_CONFIRM") != "I_UNDERSTAND":
                raise RuntimeError(
                    "LIVE mode requires env var AUTONOME_LIVE_CONFIRM=I_UNDERSTAND. "
                    "This is a deliberate safety gate. Set it explicitly to trade live."
                )
            log.critical("LIVE TRADING MODE — REAL MONEY AT RISK")

        cfg_path = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
        sec_path = os.path.join(os.path.dirname(__file__), "../../config/secrets.yaml")
        with open(cfg_path) as f:
            settings = yaml.safe_load(f)
        with open(sec_path) as f:
            secrets = yaml.safe_load(f)

        self.cfg = settings["broker"]
        self.base_url = self.cfg["paper_url"] if self.mode == "PAPER" else self.cfg["live_url"]
        self.data_url = self.cfg["data_url"]
        self.api_key = secrets["alpaca"]["api_key"]
        self.api_secret = secrets["alpaca"]["api_secret"]

        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        })

        # active if account fetch succeeds
        self._account: Optional[Account] = None

    # ── helpers ──────────────────────────────────────────────────────────
    def _get(self, path: str) -> dict:
        url = urljoin(self.base_url, path)
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        url = urljoin(self.base_url, path)
        r = self.session.post(url, json=payload, timeout=15)
        # Alpaca returns 403 if keys wrong, 422 if bad payload
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {"message": r.text}
            raise BrokerError(f"{r.status_code}: {body}", code=r.status_code, body=body)
        return r.json()

    def _delete(self, path: str) -> None:
        url = urljoin(self.base_url, path)
        r = self.session.delete(url, timeout=15)
        r.raise_for_status()

    # ── account ──────────────────────────────────────────────────────────
    def fetch_account(self) -> Account:
        raw = self._get("/v2/account")
        self._account = Account(
            equity=float(raw["equity"]),
            buying_power=float(raw["buying_power"]),
            cash=float(raw["cash"]),
            daytrade_count=int(raw.get("daytrade_count", 0)),
            status=raw["status"],
            margin_enabled=bool(raw.get("margin_enabled", False)),
        )
        return self._account

    def get_account(self) -> Account:
        if self._account is None:
            return self.fetch_account()
        return self._account

    # ── positions ────────────────────────────────────────────────────────
    def list_positions(self) -> List[Position]:
        raw = self._get("/v2/positions")
        out = []
        for p in raw:
            out.append(Position(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                current_price=float(p["current_price"]),
                unrealized_pl=float(p["unrealized_pl"]),
                unrealized_plpc=float(p["unrealized_plpc"]),
            ))
        return out

    def get_position(self, symbol: str) -> Optional[Position]:
        for p in self.list_positions():
            if p.symbol == symbol:
                return p
        return None

    # ── orders ───────────────────────────────────────────────────────────
    def submit_order(self, symbol: str, side: str, qty: float,
                     order_type: str = "market",
                     time_in_force: str = "day",
                     stop_price: Optional[float] = None,
                     limit_price: Optional[float] = None,
                     extra: Optional[dict] = None) -> OrderResult:
        payload = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "qty": str(round(qty, 8)),
        }
        if stop_price:
            payload["stop_price"] = str(round(stop_price, 2))
        if limit_price:
            payload["limit_price"] = str(round(limit_price, 2))
        if extra:
            payload.update(extra)

        try:
            raw = self._post("/v2/orders", payload)
        except BrokerError as e:
            return OrderResult(
                id="", symbol=symbol, side=side, qty=qty,
                filled_avg_price=None, status="rejected", error=str(e.body), filled_qty=0.0
            )

        return OrderResult(
            id=raw["id"],
            symbol=raw["symbol"],
            side=raw["side"],
            qty=float(raw["qty"]),
            filled_avg_price=float(raw["filled_avg_price"]) if raw.get("filled_avg_price") else None,
            status=raw["status"],
            filled_qty=float(raw["filled_qty"]) if raw.get("filled_qty") else 0.0,
        )

    def cancel_all_orders(self) -> None:
        self._delete("/v2/orders")

    def get_order(self, order_id: str) -> Optional[OrderResult]:
        try:
            raw = self._get(f"/v2/orders/{order_id}")
        except requests.HTTPError:
            return None
        return OrderResult(
            id=raw["id"],
            symbol=raw["symbol"],
            side=raw["side"],
            qty=float(raw["qty"]),
            filled_avg_price=float(raw["filled_avg_price"]) if raw.get("filled_avg_price") else None,
            status=raw["status"],
            filled_qty=float(raw["filled_qty"]) if raw.get("filled_qty") else 0.0,
        )

    def list_orders(self, status: str = "open", limit: int = 500) -> List[dict]:
        """Fetch orders by status. Returns raw dict list."""
        try:
            return self._get(f"/v2/orders?status={status}&limit={limit}")
        except requests.HTTPError:
            return []

    def cancel_order(self, order_id: str) -> None:
        """Cancel a specific order by ID."""
        try:
            self._delete(f"/v2/orders/{order_id}")
        except requests.HTTPError as e:
            log.warning("Cancel order %s failed: %s", order_id, e)

    # ── market clock ─────────────────────────────────────────────────────
    def is_market_open(self) -> bool:
        raw = self._get("/v2/clock")
        return raw.get("is_open", False)

    # ── asset check ──────────────────────────────────────────────────────
    def is_tradable(self, symbol: str) -> bool:
        try:
            raw = self._get(f"/v2/assets/{symbol}")
            return raw.get("tradable", False) and raw.get("status") == "active"
        except requests.HTTPError:
            return False

    def get_asset(self, symbol: str) -> Optional[dict]:
        try:
            return self._get(f"/v2/assets/{symbol}")
        except requests.HTTPError:
            return None

    def is_margin_enabled(self) -> bool:
        try:
            raw = self._get("/v2/account")
            return bool(raw.get("margin_enabled", False))
        except requests.HTTPError:
            return False


class BrokerError(Exception):
    def __init__(self, msg: str, code: int, body: dict):
        super().__init__(msg)
        self.code = code
        self.body = body
