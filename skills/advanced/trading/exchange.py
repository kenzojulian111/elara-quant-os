from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
from typing import Any


class ExchangeConnector:
    def __init__(self, config, logger: logging.Logger | None = None):
        self.config = config
        self.log = logger or logging.getLogger("elara.trading.exchange")
        self.async_exchange = None
        self._last_ws_seq: dict[str, int] = {}

    def _market_precision(self, symbol: str) -> tuple[int, int]:
        markets = getattr(self.async_exchange, "markets", {}) or {}
        info = markets.get(symbol, {}) if isinstance(markets, dict) else {}
        precision = info.get("precision", {}) if isinstance(info, dict) else {}
        return int(precision.get("amount", 6) or 6), int(precision.get("price", 2) or 2)

    def _apply_exchange_precision(self, symbol: str, amount: D, price: D, side: str = "buy") -> tuple[D, D]:
        amount_prec, price_prec = self._market_precision(symbol)
        px_quant = D("1").scaleb(-price_prec)
        if self.async_exchange and hasattr(self.async_exchange, "amount_to_precision"):
            qty = D(str(self.async_exchange.amount_to_precision(symbol, amount)))
        else:
            qty_quant = D("1").scaleb(-amount_prec)
            qty = D(amount).quantize(qty_quant, rounding=ROUND_DOWN)
        px = D(price).quantize(px_quant, rounding=ROUND_UP if str(side or "").lower() == "sell" else ROUND_DOWN)
        return qty, px

    @staticmethod
    def extract_order_amount(order: dict[str, Any], default: D) -> D:
        try:
            return D(str(order.get("filled", order.get("amount", default))))
        except Exception:
            return D(default)

    @staticmethod
    def extract_order_price(order: dict[str, Any], default: D) -> D:
        try:
            return D(str(order.get("average", order.get("price", default))))
        except Exception:
            return D(default)

    async def get_bid_ask_spread_pct(self, symbol: str) -> float:
        if not self.async_exchange:
            return 0.0
        ticker = {}
        try:
            ticker = await self.async_exchange.fetch_ticker(symbol)
        except Exception:
            ticker = {}
        ask = float(ticker.get("ask") or 0.0)
        bid = float(ticker.get("bid") or 0.0)
        if ask <= 0 or bid <= 0:
            order_book = await self.async_exchange.fetch_order_book(symbol, limit=5)
            asks = order_book.get("asks") or []
            bids = order_book.get("bids") or []
            ask = float((asks[0][0] if asks else 0.0) or 0.0)
            bid = float((bids[0][0] if bids else 0.0) or 0.0)
        if ask <= 0 or bid <= 0:
            return 0.0
        mid = (ask + bid) / 2.0
        if mid <= 0:
            return 0.0
        return ((ask - bid) / mid) * 100.0

    async def create_market_order(self, symbol: str, side: str, amount: D, params: dict[str, Any] | None = None):
        if not self.async_exchange:
            return None

        spread_pct = await self.get_bid_ask_spread_pct(symbol)
        max_spread = float(getattr(self.config, "MARKET_ORDER_MAX_SPREAD_PCT", 1.0) or 1.0)
        if spread_pct > max_spread:
            return None

        if bool(getattr(self.config, "ORDER_USE_LIMIT_CHASE", False)):
            order_book = await self.async_exchange.fetch_order_book(symbol, limit=5)
            asks = order_book.get("asks") or []
            bids = order_book.get("bids") or []
            ref_price = asks[0][0] if str(side or "").lower() == "buy" else bids[0][0]
            qty, px = self._apply_exchange_precision(symbol, D(amount), D(str(ref_price)), side=side)
            order = await self.async_exchange.create_limit_order(symbol, side, qty, px)
            deadline = asyncio.get_running_loop().time() + float(getattr(self.config, "ORDER_CHASE_TIMEOUT_SEC", 3.0) or 3.0)
            poll_sec = float(getattr(self.config, "ORDER_CHASE_POLL_SEC", 0.3) or 0.3)
            while asyncio.get_running_loop().time() < deadline:
                fetched = await self.async_exchange.fetch_order(order["id"], symbol)
                if str(fetched.get("status") or "").lower() in {"closed", "filled"}:
                    return fetched
                await asyncio.sleep(poll_sec)
            await self.async_exchange.cancel_order(order["id"], symbol)
            return None

        return await self.async_exchange.create_market_order(symbol, side, amount, params=params)

    async def fetch_order(self, symbol: str, order_id: str):
        if not self.async_exchange:
            return None
        return await self.async_exchange.fetch_order(order_id, symbol)

    def _check_ws_sequence(self, stream_key: str, payload: dict[str, Any]) -> bool:
        if not bool(getattr(self.config, "WS_SEQUENCE_CHECK_ENABLED", True)):
            return True
        start_seq = int(payload.get("U", payload.get("u", 0)) or 0)
        end_seq = int(payload.get("u", start_seq) or start_seq)
        previous = self._last_ws_seq.get(stream_key)
        self._last_ws_seq[stream_key] = end_seq
        if previous is None:
            return True
        return start_seq <= (previous + 1)

    def get_symbol_health(self, symbol: str) -> dict[str, Any]:
        markets = getattr(self.async_exchange, "markets", {}) or {}
        info = markets.get(symbol, {}) if isinstance(markets, dict) else {}
        status = str((info.get("info", {}) or {}).get("status", "") or "").lower()
        risk_flags = [str(item).lower() for item in getattr(self.config, "DELIST_STATUS_RISK_FLAGS", []) or []]
        if any(flag in status for flag in risk_flags):
            return {"risk": "delist_warning", "status": status}
        return {"risk": "ok", "status": status}

    async def fetch_balances(self) -> dict[str, dict[str, D]]:
        if not self.async_exchange:
            return {}
        raw = await self.async_exchange.fetch_balance()
        out: dict[str, dict[str, D]] = defaultdict(dict)
        for bucket in ("free", "used", "total"):
            for asset, value in dict(raw.get(bucket) or {}).items():
                out[str(asset)][bucket] = D(str(value))
        return dict(out)

    def update_runtime_credentials(self, api_key=None, api_secret=None):
        if api_key:
            setattr(self.config, "API_KEY", api_key)
        if api_secret:
            setattr(self.config, "API_SECRET", api_secret)
        return True

    async def ensure_fresh_credentials(self):
        return True

