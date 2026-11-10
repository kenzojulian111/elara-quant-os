import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from runtime.interfaces.web_factory import create_web_app


class _DummySkill:
    def __init__(self):
        self.skill_manager = None


class _CommandHandler(BaseHTTPRequestHandler):
    last_body = None

    def log_message(self, _format, *_args):
        return

    def _set_headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_POST(self):
        if self.path.startswith("/command"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            _CommandHandler.last_body = body
            self._set_headers(200)
            self.wfile.write(b"{\"status\":\"queued\"}")
            return
        self._set_headers(404)
        self.wfile.write(b"{}")


class TradingCommandHttpFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_trading_command_")
        cls._db_path = os.path.join(cls._tmpdir, "trading.sqlite")
        cls._command_path = os.path.join(cls._tmpdir, "trading_command.json")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "SECRET_KEY": getattr(Config, "SECRET_KEY", ""),
            "JWT_SECRET_KEY": getattr(Config, "JWT_SECRET_KEY", ""),
            "WEB_CSRF_PROTECT": getattr(Config, "WEB_CSRF_PROTECT", True),
            "WEB_SECURITY_HEADERS": getattr(Config, "WEB_SECURITY_HEADERS", True),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
            "ALLOW_GUEST_DASHBOARD": getattr(Config, "ALLOW_GUEST_DASHBOARD", False),
            "API_REPLAY_PROTECT_ENABLED": getattr(Config, "API_REPLAY_PROTECT_ENABLED", False),
            "TRADING_FILE_IPC_FALLBACK_ENABLED": getattr(Config, "TRADING_FILE_IPC_FALLBACK_ENABLED", True),
            "TRADING_COMMAND_PATH": getattr(Config, "TRADING_COMMAND_PATH", ""),
            "TRADER_API_BASE_URL": getattr(Config, "TRADER_API_BASE_URL", ""),
            "TRADER_API_TIMEOUT_SEC": getattr(Config, "TRADER_API_TIMEOUT_SEC", 2.0),
        }
        cls._env_backup = os.environ.get("REDIS_URL")

        os.environ["REDIS_URL"] = ""
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.SECRET_KEY = "test-secret"
        Config.JWT_SECRET_KEY = "test-secret"
        Config.WEB_CSRF_PROTECT = False
        Config.WEB_SECURITY_HEADERS = False
        Config.DB_AUTO_CREATE_TABLES = True
        Config.ALLOW_GUEST_DASHBOARD = True
        Config.API_REPLAY_PROTECT_ENABLED = False
        Config.TRADING_FILE_IPC_FALLBACK_ENABLED = False
        Config.TRADING_COMMAND_PATH = cls._command_path
        Config.TRADER_API_TIMEOUT_SEC = 1.0

        cls._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CommandHandler)
        cls._port = cls._httpd.server_port
        cls._thread = threading.Thread(target=cls._httpd.serve_forever, daemon=True)
        cls._thread.start()

        Config.TRADER_API_BASE_URL = f"http://127.0.0.1:{cls._port}"

        cls.db_manager = DatabaseManager()
        cls.app, cls.socketio = create_web_app(_DummySkill(), cls.db_manager)
        cls.app.testing = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        if cls._httpd:
            cls._httpd.shutdown()
            cls._httpd.server_close()
        if cls._thread:
            cls._thread.join(timeout=1)
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        if cls._env_backup is None:
            os.environ.pop("REDIS_URL", None)
        else:
            os.environ["REDIS_URL"] = cls._env_backup
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def test_trading_command_uses_http_fallback(self):
        resp = self.client.post(
            "/api/trading/command",
            json={"cmd": "stop", "reason": "test", "user_id": "tester"},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        payload = resp.get_json() or {}
        self.assertEqual(payload.get("transport"), "http")
        self.assertIsNotNone(_CommandHandler.last_body)


if __name__ == "__main__":
    unittest.main()

