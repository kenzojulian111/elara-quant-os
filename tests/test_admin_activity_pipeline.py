import os
import shutil
import tempfile
import unittest

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from runtime.interfaces.web_factory import create_web_app


class _DummySkill:
    def __init__(self):
        self.skill_manager = None


class AdminActivityPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_activity_")
        cls._db_path = os.path.join(cls._tmpdir, "activity_pipeline.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "SECRET_KEY": getattr(Config, "SECRET_KEY", ""),
            "JWT_SECRET_KEY": getattr(Config, "JWT_SECRET_KEY", ""),
            "WEB_CSRF_PROTECT": getattr(Config, "WEB_CSRF_PROTECT", True),
            "WEB_SECURITY_HEADERS": getattr(Config, "WEB_SECURITY_HEADERS", True),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
            "JWT_REVOKE_ENABLED": getattr(Config, "JWT_REVOKE_ENABLED", True),
            "WEB_ACTIVITY_ALERT_TG_ENABLED": getattr(Config, "WEB_ACTIVITY_ALERT_TG_ENABLED", False),
        }

        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.SECRET_KEY = "test-secret"
        Config.JWT_SECRET_KEY = "test-secret"
        Config.WEB_CSRF_PROTECT = False
        Config.WEB_SECURITY_HEADERS = False
        Config.DB_AUTO_CREATE_TABLES = True
        Config.JWT_REVOKE_ENABLED = True
        Config.WEB_ACTIVITY_ALERT_TG_ENABLED = False

        cls.db_manager = DatabaseManager()
        cls.app, _ = create_web_app(_DummySkill(), cls.db_manager)
        cls.app.testing = True
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db.create_all()
            cls.db_manager.create_user(
                email="admin@test.local",
                password="secret123",
                name="Admin",
                role="admin",
            )

    @classmethod
    def tearDownClass(cls):
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def _login_admin(self) -> str:
        res = self.client.post(
            "/api/login",
            json={"email": "admin@test.local", "password": "secret123"},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        payload = res.get_json() or {}
        token = str(payload.get("access_token") or "")
        self.assertTrue(token)
        return token

    @staticmethod
    def _auth_header(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_admin_activity_pipeline_end_to_end(self):
        token = self._login_admin()
        headers = self._auth_header(token)

        with self.app.app_context():
            for _ in range(10):
                self.db_manager.log_user_activity(
                    username="bot-danger",
                    action="trading_command_rejected",
                    path="/api/trading/command",
                    method="POST",
                    status_code=401,
                    details="invalid_payload",
                )
            for _ in range(12):
                self.db_manager.log_user_activity(
                    username="bot-danger",
                    action="trading_command_failed",
                    path="/api/trading/command",
                    method="POST",
                    status_code=503,
                    details="exchange_down",
                )
            self.db_manager.log_user_activity(
                username="admin@test.local",
                action="admin_create_user_success",
                path="/api/admin/users",
                method="POST",
                status_code=201,
                details="user_x",
            )
            self.db_manager.log_user_activity(
                username="admin@test.local",
                action="login_redirect",
                path="/login",
                method="GET",
                status_code=302,
                details="redirect_to_dashboard",
            )

        page_res = self.client.get("/api/admin/activity?limit=5&offset=0", headers=headers)
        self.assertEqual(page_res.status_code, 200, page_res.get_data(as_text=True))
        page_payload = page_res.get_json() or {}
        self.assertIn("items", page_payload)
        self.assertIn("total", page_payload)
        self.assertLessEqual(len(page_payload.get("items") or []), 5)

        filtered_res = self.client.get("/api/admin/activity?status_group=5xx&limit=50", headers=headers)
        self.assertEqual(filtered_res.status_code, 200, filtered_res.get_data(as_text=True))
        filtered_payload = filtered_res.get_json() or {}
        for row in filtered_payload.get("items") or []:
            code = int(row.get("status_code") or 0)
            self.assertGreaterEqual(code, 500)
            self.assertLess(code, 600)

        filtered_redirect_res = self.client.get("/api/admin/activity?status_group=3xx&limit=50", headers=headers)
        self.assertEqual(filtered_redirect_res.status_code, 200, filtered_redirect_res.get_data(as_text=True))
        filtered_redirect_payload = filtered_redirect_res.get_json() or {}
        redirect_rows = filtered_redirect_payload.get("items") or []
        self.assertGreaterEqual(len(redirect_rows), 1)
        for row in redirect_rows:
            code = int(row.get("status_code") or 0)
            self.assertGreaterEqual(code, 300)
            self.assertLess(code, 400)

        stats_res = self.client.get("/api/admin/activity/stats?top_n=5", headers=headers)
        self.assertEqual(stats_res.status_code, 200, stats_res.get_data(as_text=True))
        stats_payload = stats_res.get_json() or {}
        self.assertIn("trend", stats_payload)
        self.assertIn("anomalies", stats_payload)
        anomalies = stats_payload.get("anomalies") or []
        self.assertTrue(any(str(row.get("type") or "") == "server_error_spike" for row in anomalies))
        trend = stats_payload.get("trend") or {}
        self.assertIn("points", trend)
        self.assertIsInstance(trend.get("points"), list)

        export_res = self.client.get("/api/admin/activity/export.csv?limit=200", headers=headers)
        self.assertEqual(export_res.status_code, 200, export_res.get_data(as_text=True))
        csv_body = export_res.get_data(as_text=True)
        self.assertIn("timestamp,username,action,endpoint,status_code,details,method,path,id", csv_body)
        self.assertIn("bot-danger", csv_body)

        chain_res = self.client.get("/api/admin/activity/chain/verify?limit=5000", headers=headers)
        self.assertEqual(chain_res.status_code, 200, chain_res.get_data(as_text=True))
        chain_payload = chain_res.get_json() or {}
        self.assertTrue(bool(chain_payload.get("ok")))
        self.assertGreaterEqual(int(chain_payload.get("checked") or 0), 1)

    def test_admin_session_listing_and_force_logout(self):
        token = self._login_admin()
        headers = self._auth_header(token)

        sessions_res = self.client.get("/api/admin/sessions?limit=100", headers=headers)
        self.assertEqual(sessions_res.status_code, 200, sessions_res.get_data(as_text=True))
        sessions_payload = sessions_res.get_json() or {}
        session_rows = sessions_payload.get("items") or []
        self.assertTrue(any(str(row.get("username") or "") == "admin@test.local" for row in session_rows))

        revoke_res = self.client.post(
            "/api/admin/sessions/force-logout",
            headers=headers,
            json={"username": "admin@test.local"},
        )
        self.assertEqual(revoke_res.status_code, 200, revoke_res.get_data(as_text=True))
        revoke_payload = revoke_res.get_json() or {}
        self.assertGreaterEqual(int(revoke_payload.get("revoked_sessions") or 0), 1)

        # Same token should fail after forced logout.
        me_res = self.client.get("/api/me", headers=headers)
        self.assertIn(me_res.status_code, {401, 422}, me_res.get_data(as_text=True))

    def test_admin_alert_rule_api_flow(self):
        token = self._login_admin()
        headers = self._auth_header(token)

        create_res = self.client.post(
            "/api/alerts/rules",
            headers=headers,
            json={
                "name": "AAPL drop",
                "market_kind": "stock",
                "symbol": "AAPL",
                "condition": "cross_below",
                "target_price": 999999,
                "cooldown_sec": 120,
                "notify_channel": "portal",
                "scope": "global",
            },
        )
        self.assertEqual(create_res.status_code, 201, create_res.get_data(as_text=True))
        create_payload = create_res.get_json() or {}
        rule = create_payload.get("rule") or {}
        rule_id = int(rule.get("id") or 0)
        self.assertGreater(rule_id, 0)
        self.assertEqual(str(rule.get("scope") or ""), "global")

        list_res = self.client.get("/api/alerts/rules?all=1&limit=200", headers=headers)
        self.assertEqual(list_res.status_code, 200, list_res.get_data(as_text=True))
        list_payload = list_res.get_json() or {}
        self.assertTrue(any(int((row or {}).get("id") or 0) == rule_id for row in (list_payload.get("items") or [])))

        patch_res = self.client.patch(
            f"/api/alerts/rules/{rule_id}",
            headers=headers,
            json={"is_active": False},
        )
        self.assertEqual(patch_res.status_code, 200, patch_res.get_data(as_text=True))
        patched = (patch_res.get_json() or {}).get("rule") or {}
        self.assertFalse(bool(patched.get("is_active", True)))

        eval_res = self.client.post("/api/alerts/evaluate", headers=headers, json={"refresh": True})
        self.assertEqual(eval_res.status_code, 200, eval_res.get_data(as_text=True))
        eval_payload = eval_res.get_json() or {}
        self.assertEqual(str(eval_payload.get("status") or ""), "ok")
        self.assertIn("result", eval_payload)

        events_res = self.client.get("/api/alerts/events?all=1&limit=50", headers=headers)
        self.assertEqual(events_res.status_code, 200, events_res.get_data(as_text=True))
        self.assertIn("items", events_res.get_json() or {})

        delete_res = self.client.delete(f"/api/alerts/rules/{rule_id}", headers=headers)
        self.assertEqual(delete_res.status_code, 200, delete_res.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

