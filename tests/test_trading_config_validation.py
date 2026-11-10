import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.advanced.trading.config import TradingConfig


class TradingConfigValidationTests(unittest.TestCase):
    def test_base_dir_points_to_project_root(self):
        cfg = TradingConfig()
        self.assertTrue(os.path.isdir(cfg.BASE_DIR))
        self.assertTrue(os.path.exists(os.path.join(cfg.BASE_DIR, "core")))

    def test_market_order_spread_zero_is_valid(self):
        original_mode = TradingConfig.MODE
        original_spread = TradingConfig.MARKET_ORDER_MAX_SPREAD_PCT
        try:
            TradingConfig.MODE = "paper"
            TradingConfig.MARKET_ORDER_MAX_SPREAD_PCT = 0.0
            errors = TradingConfig.validate_runtime_schema()
            self.assertFalse(any("MARKET_ORDER_MAX_SPREAD_PCT" in e for e in errors))
        finally:
            TradingConfig.MODE = original_mode
            TradingConfig.MARKET_ORDER_MAX_SPREAD_PCT = original_spread

    def test_market_order_spread_negative_is_rejected(self):
        original_mode = TradingConfig.MODE
        original_spread = TradingConfig.MARKET_ORDER_MAX_SPREAD_PCT
        try:
            TradingConfig.MODE = "paper"
            TradingConfig.MARKET_ORDER_MAX_SPREAD_PCT = -0.1
            errors = TradingConfig.validate_runtime_schema()
            self.assertTrue(any("MARKET_ORDER_MAX_SPREAD_PCT" in e for e in errors))
        finally:
            TradingConfig.MODE = original_mode
            TradingConfig.MARKET_ORDER_MAX_SPREAD_PCT = original_spread


if __name__ == "__main__":
    unittest.main()

