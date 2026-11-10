import os
import shutil
import tempfile
import time
import unittest

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from runtime.interfaces.web_factory import create_web_app


class _SlowStockSkill:
    def __init__(self):
        self.fetch_calls = 0

    def get_screen_snapshot(self, force_refresh: bool = False, wait_timeout_sec: float = 0.0):
        return {"running": False, "results": [], "updated_at": 0.0, "error": "no_data"}

    def _fetch_bulk_ohlcv(self, _symbols):
        self.fetch_calls += 1
        raise AssertionError("blocking stock fallback should not run in market realtime")

    def _fetch_ohlcv(self, _symbol):
        self.fetch_calls += 1
        raise AssertionError("blocking stock fallback should not run in market realtime")


class _SlowCryptoSkill:
    def __init__(self):
        self.fetch_calls = 0

    def get_screen_snapshot(self, force_refresh: bool = False, wait_timeout_sec: float = 0.0):
        return {"running": False, "results": [], "updated_at": 0.0, "error": "no_data"}

    def _fetch_ohlcv_openbb(self, _symbol):
        self.fetch_calls += 1
        raise AssertionError("blocking crypto fallback should not run in market realtime")

    def _fetch_ohlcv_yfinance(self, _symbol):
        self.fetch_calls += 1
        raise AssertionError("blocking crypto fallback should not run in market realtime")


class _DummySkillManager:
    def __init__(self, stock_skill, crypto_skill):
        self.intent_map = {
            "screen_stocks": stock_skill,
            "screen_crypto": crypto_skill,
        }


class _DummyElaraSkill:
    def __init__(self, stock_skill, crypto_skill):
        self.skill_manager = _DummySkillManager(stock_skill, crypto_skill)


class MarketRealtimeNonBlockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_market_rt_")
        cls._db_path = os.path.join(cls._tmpdir, "market_rt.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "SECRET_KEY": getattr(Config, "SECRET_KEY", ""),
            "JWT_SECRET_KEY": getattr(Config, "JWT_SECRET_KEY", ""),
            "WEB_CSRF_PROTECT": getattr(Config, "WEB_CSRF_PROTECT", True),
            "WEB_SECURITY_HEADERS": getattr(Config, "WEB_SECURITY_HEADERS", True),
            "ALLOW_GUEST_DASHBOARD": getattr(Config, "ALLOW_GUEST_DASHBOARD", False),
            "WEB_MARKET_ALLOW_BLOCKING_FALLBACK": getattr(Config, "WEB_MARKET_ALLOW_BLOCKING_FALLBACK", False),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
            "WEB_ACTIVITY_ALERT_TG_ENABLED": getattr(Config, "WEB_ACTIVITY_ALERT_TG_ENABLED", False),
        }

        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.SECRET_KEY = "test-secret"
        Config.JWT_SECRET_KEY = "test-secret"
        Config.WEB_CSRF_PROTECT = False
        Config.WEB_SECURITY_HEADERS = False
        Config.ALLOW_GUEST_DASHBOARD = True
        Config.WEB_MARKET_ALLOW_BLOCKING_FALLBACK = False
        Config.DB_AUTO_CREATE_TABLES = True
        Config.WEB_ACTIVITY_ALERT_TG_ENABLED = False

        cls.stock_skill = _SlowStockSkill()
        cls.crypto_skill = _SlowCryptoSkill()
        cls.db_manager = DatabaseManager()
        cls.app, _ = create_web_app(_DummyElaraSkill(cls.stock_skill, cls.crypto_skill), cls.db_manager)
        cls.app.testing = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def test_market_realtime_skips_blocking_fallback_without_force_refresh(self):
        started = time.perf_counter()
        response = self.client.get("/api/market/realtime?limit=30")
        elapsed = time.perf_counter() - started

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json() or {}
        self.assertIn("stock", payload)
        self.assertIn("crypto", payload)
        self.assertLess(elapsed, 2.5, f"market realtime too slow: {elapsed:.3f}s")
        self.assertEqual(self.stock_skill.fetch_calls, 0)
        self.assertEqual(self.crypto_skill.fetch_calls, 0)


