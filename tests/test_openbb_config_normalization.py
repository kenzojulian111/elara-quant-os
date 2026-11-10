import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from skills.CryptoScreenerSkill import CryptoScreenerSkill
from skills.StockScreenerSkill import StockScreenerSkill


class OpenBBConfigNormalizationTests(unittest.TestCase):
    CFG_KEYS = [
        "OPENBB_ENABLED",
        "OPENBB_ONLY",
        "OPENBB_STRICT",
        "STOCK_DATA_PROVIDER",
        "OPENBB_PROVIDER_STOCK",
        "OPENBB_PROVIDER_CRYPTO",
        "CRYPTO_SCREEN_USE_OPENBB",
    ]

    def setUp(self):
        self._cfg = {k: getattr(Config, k) for k in self.CFG_KEYS}
        Config.OPENBB_ENABLED = False
        Config.OPENBB_ONLY = True
        Config.OPENBB_STRICT = True
        Config.STOCK_DATA_PROVIDER = "yfinance"
        Config.OPENBB_PROVIDER_STOCK = "yfinance"
        Config.OPENBB_PROVIDER_CRYPTO = "yfinance"
        Config.CRYPTO_SCREEN_USE_OPENBB = False

    def tearDown(self):
        for key, value in self._cfg.items():
            setattr(Config, key, value)

    def test_stock_screener_forces_openbb_enabled_when_openbb_only(self):
        skill = StockScreenerSkill()
        self.assertTrue(skill.openbb_only)
        self.assertTrue(skill.openbb_enabled)
        self.assertEqual("openbb", skill.provider)

    def test_crypto_screener_forces_openbb_enabled_when_openbb_only(self):
        skill = CryptoScreenerSkill()
        self.assertTrue(skill.openbb_only)
        self.assertTrue(skill.openbb_enabled)
        self.assertTrue(skill.use_openbb)


if __name__ == "__main__":
    unittest.main()

