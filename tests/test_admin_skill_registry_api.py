import os
import shutil
import tempfile
import unittest

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from runtime.interfaces.web_factory import create_web_app


class _FakeSkillManager:
    def __init__(self):
        self.refresh_calls = []

    def get_markdown_registry_stats(self):
        return {
            "enabled": True,
            "available": True,
            "count": 3,
            "watch_enabled": True,
            "refresh_count": 5,
        }

    def refresh_markdown_registry(self, force: bool = False, reason: str = "manual"):
        self.refresh_calls.append({"force": bool(force), "reason": str(reason or "")})
        return {
            "enabled": True,
            "available": True,
            "refreshed": bool(force),
            "stats": self.get_markdown_registry_stats(),
        }


class _DummySkill:
    def __init__(self, manager):
        self.skill_manager = manager


class AdminSkillRegistryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_admin_skill_registry_")
        cls._db_path = os.path.join(cls._tmpdir, "admin-skill-registry.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "SECRET_KEY": getattr(Config, "SECRET_KEY", ""),
            "JWT_SECRET_KEY": getattr(Config, "JWT_SECRET_KEY", ""),
            "WEB_CSRF_PROTECT": getattr(Config, "WEB_CSRF_PROTECT", True),
            "WEB_SECURITY_HEADERS": getattr(Config, "WEB_SECURITY_HEADERS", True),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
            "WEB_ADMIN_MFA_REQUIRED": getattr(Config, "WEB_ADMIN_MFA_REQUIRED", True),
        }

        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.SECRET_KEY = "test-secret"
        Config.JWT_SECRET_KEY = "test-secret"
        Config.WEB_CSRF_PROTECT = False
        Config.WEB_SECURITY_HEADERS = False
        Config.DB_AUTO_CREATE_TABLES = True
        Config.WEB_ADMIN_MFA_REQUIRED = False

        cls.db_manager = DatabaseManager()
        cls.fake_manager = _FakeSkillManager()
        cls.app, cls.socketio = create_web_app(_DummySkill(cls.fake_manager), cls.db_manager)
        cls.app.testing = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()
        cls.db_manager.create_user("admin@test.local", "secret123", role="admin")
        cls.db_manager.create_user("user@test.local", "secret123", role="user")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    @staticmethod
    def _auth_header(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _login_token(self, email: str, password: str = "secret123") -> str:
        res = self.client.post("/api/signin", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        payload = res.get_json() or {}
        token = str(payload.get("access_token") or "")
        self.assertTrue(token)
        return token

    def test_admin_can_view_and_refresh_markdown_registry(self):
        token = self._login_token("admin@test.local")
        headers = self._auth_header(token)

        view = self.client.get("/api/admin/skills/registry", headers=headers)
        self.assertEqual(view.status_code, 200, view.get_data(as_text=True))
        view_payload = view.get_json() or {}
        self.assertEqual(view_payload.get("viewer_scope"), "admin")
        self.assertTrue(bool(view_payload.get("available")))
        self.assertIn("count", view_payload)

        refresh = self.client.post(
            "/api/admin/skills/registry/refresh",
            headers=headers,
            json={"force": True, "reason": "ops_test"},
        )
        self.assertEqual(refresh.status_code, 200, refresh.get_data(as_text=True))
        refresh_payload = refresh.get_json() or {}
        self.assertEqual(refresh_payload.get("viewer_scope"), "admin")
        self.assertTrue(bool(refresh_payload.get("refreshed")))
        self.assertTrue(self.fake_manager.refresh_calls)
        self.assertEqual(self.fake_manager.refresh_calls[-1]["reason"], "ops_test")

    def test_non_admin_is_blocked(self):
        token = self._login_token("user@test.local")
        headers = self._auth_header(token)
        res = self.client.get("/api/admin/skills/registry", headers=headers)
        self.assertEqual(res.status_code, 403, res.get_data(as_text=True))


