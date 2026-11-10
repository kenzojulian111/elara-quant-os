import unittest
from decimal import Decimal

from skills.advanced.trading.analyzers import RiskManager, TradeDirection

try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st
    HYPOTHESIS_AVAILABLE = True
except Exception:
    HYPOTHESIS_AVAILABLE = False


if HYPOTHESIS_AVAILABLE:
    DECIMAL_PRICE = st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("100000"),
        places=6,
        allow_nan=False,
        allow_infinity=False,
    )
    DECIMAL_SIZE = st.decimals(
        min_value=Decimal("0.000001"),
        max_value=Decimal("1000"),
        places=6,
        allow_nan=False,
        allow_infinity=False,
    )

    class TestRiskMathProperties(unittest.TestCase):
        def setUp(self):
            self.rm = RiskManager(
                total_balance=Decimal("10000"),
                risk_per_trade_pct=Decimal("1"),
                fee_pct=Decimal("0.1"),
            )

        @settings(max_examples=120, deadline=None)
        @given(entry=DECIMAL_PRICE, exit_p=DECIMAL_PRICE, size=DECIMAL_SIZE)
        def test_trade_outcome_invariants(self, entry: Decimal, exit_p: Decimal, size: Decimal):
            long_res = self.rm.calculate_trade_outcome(entry, exit_p, size, TradeDirection.LONG)
            short_res = self.rm.calculate_trade_outcome(entry, exit_p, size, TradeDirection.SHORT)

            self.assertGreaterEqual(long_res.total_fees, Decimal("0"))
            self.assertGreaterEqual(short_res.total_fees, Decimal("0"))
            self.assertEqual(long_res.net_pnl, long_res.pnl - long_res.total_fees)
            self.assertEqual(short_res.net_pnl, short_res.pnl - short_res.total_fees)

        @settings(max_examples=120, deadline=None)
        @given(entry=DECIMAL_PRICE, exit_p=DECIMAL_PRICE, size=DECIMAL_SIZE)
        def test_long_short_symmetry(self, entry: Decimal, exit_p: Decimal, size: Decimal):
            long_res = self.rm.calculate_trade_outcome(entry, exit_p, size, TradeDirection.LONG)
            short_res = self.rm.calculate_trade_outcome(entry, exit_p, size, TradeDirection.SHORT)
            self.assertEqual(long_res.pnl, -short_res.pnl)
            self.assertEqual(long_res.total_fees, short_res.total_fees)

        @settings(max_examples=120, deadline=None)
        @given(entry=DECIMAL_PRICE, stop=DECIMAL_PRICE)
        def test_position_size_non_negative(self, entry: Decimal, stop: Decimal):
            assume(entry != stop)
            size = self.rm.calculate_position_size(entry, stop)
            self.assertGreaterEqual(size, Decimal("0"))

else:
    class TestRiskMathProperties(unittest.TestCase):
        @unittest.skip("hypothesis not installed")
        def test_hypothesis_missing(self):
            self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()

