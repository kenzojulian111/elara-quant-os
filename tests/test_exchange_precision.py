import logging
import unittest
from decimal import Decimal
import os
import sys
import asyncio

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.exchange import ExchangeConnector


class _Cfg:
    EXCHANGE_NAME = "binance"
    EXCHANGE_RATE_LIMIT_MAX_CALLS = 1100
    EXCHANGE_RATE_LIMIT_PERIOD_SEC = 60
    EXCHANGE_API_ERROR_THRESHOLD = 3
    EXCHANGE_API_PAUSE_SEC = 3600
    ORDER_PRICE_SIDE_ROUNDING_ENABLED = True
    ORDER_USE_LIMIT_CHASE = False
    MODE = "live"
    MARKET_ORDER_MAX_SPREAD_PCT = 0.5


class _DummyExchange:
    markets = {"BTC/USDT": {"precision": {"amount": 5, "price": 2}}}

    @staticmethod
    def amount_to_precision(symbol, amount):
        return f"{float(amount):.5f}"

    @staticmethod
    def price_to_precision(symbol, price):
        return f"{float(price):.2f}"

    async def create_market_order(self, symbol, side, amount, params=None):
        _ = params
        return {"id": "mkt1", "symbol": symbol, "side": side, "amount": amount}


class ExchangePrecisionTests(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger("test.exchange.precision")
        self.connector = ExchangeConnector(_Cfg(), logger)
        self.connector.async_exchange = _DummyExchange()

    def test_apply_exchange_precision(self):
        qty, px = self.connector._apply_exchange_precision(
            "BTC/USDT",
            Decimal("0.1234567"),
            Decimal("43210.98765"),
            side="buy",
        )
        self.assertEqual(qty, Decimal("0.12346"))
        self.assertEqual(px, Decimal("43210.98"))

    def test_apply_exchange_precision_sell_round_up_price(self):
        qty, px = self.connector._apply_exchange_precision(
            "BTC/USDT",
            Decimal("0.120001"),
            Decimal("43210.981"),
            side="sell",
        )
        self.assertEqual(qty, Decimal("0.12000"))
        self.assertEqual(px, Decimal("43210.99"))

    def test_extract_order_helpers(self):
        order = {"filled": "0.05", "average": "123.45"}
        amt = self.connector.extract_order_amount(order, Decimal("0"))
        price = self.connector.extract_order_price(order, Decimal("0"))
        self.assertEqual(amt, Decimal("0.05"))
        self.assertEqual(price, Decimal("123.45"))

    def test_market_order_blocked_when_spread_too_wide(self):
        async def _run():
            self.connector.get_bid_ask_spread_pct = lambda symbol: asyncio.sleep(0, result=1.2)
            order = await self.connector.create_market_order("BTC/USDT", "buy", Decimal("0.1"))
            return order

        order = asyncio.run(_run())
        self.assertIsNone(order)


if __name__ == "__main__":
    unittest.main()

