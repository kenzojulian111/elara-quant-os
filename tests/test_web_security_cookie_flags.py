import os
import shutil
import tempfile
import unittest

from config.settings import Config
from database.database_manager import DatabaseManager
from runtime.interfaces import web_factory as web_factory_module
from runtime.interfaces.web_factory import create_web_app


class _DummySkill:
    def __init__(self):
        self.skill_manager = None


class WebSecurityCookieFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_web_cookie_")
        cls._db_path = os.path.join(cls._tmpdir, "cookie_flags.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "SECRET_KEY": getattr(Config, "SECRET_KEY", ""),
            "JWT_SECRET_KEY": getattr(Config, "JWT_SECRET_KEY", ""),
            "WEB_SECURITY_HEADERS": getattr(Config, "WEB_SECURITY_HEADERS", True),
            "WEB_FORCE_HTTPS": getattr(Config, "WEB_FORCE_HTTPS", False),
            "WEB_CSRF_PROTECT": getattr(Config, "WEB_CSRF_PROTECT", True),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
            "ALLOW_DEV_BACKDOOR": getattr(Config, "ALLOW_DEV_BACKDOOR", False),
        }

        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.SECRET_KEY = "test-secret-cookie"
        Config.JWT_SECRET_KEY = "test-secret-cookie"
        Config.WEB_CSRF_PROTECT = True
        Config.DB_AUTO_CREATE_TABLES = True
        Config.ALLOW_DEV_BACKDOOR = False

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    @staticmethod
    def _new_client():
        db_manager = DatabaseManager()
        app, _ = create_web_app(_DummySkill(), db_manager)
        app.testing = True
        return app.test_client()

    def test_session_cookie_not_secure_on_http_when_force_https_disabled(self):
        if getattr(web_factory_module, "Talisman", None) is None:
            self.skipTest("Flask-Talisman not installed in this environment")

        Config.WEB_SECURITY_HEADERS = True
        Config.WEB_FORCE_HTTPS = False

        client = self._new_client()
        resp = client.get("/login")

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        set_cookie = str(resp.headers.get("Set-Cookie") or "")
        self.assertIn("session=", set_cookie)
        self.assertNotIn("Secure;", set_cookie)


if __name__ == "__main__":
    unittest.main()

