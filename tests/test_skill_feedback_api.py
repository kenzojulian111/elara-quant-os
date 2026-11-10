import os
import shutil
import tempfile
import unittest

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import db
from runtime.interfaces.web_factory import create_web_app


class _FakeLLMSkill:
    def __init__(self):
        self.feedback_calls = []

    def get_markdown_skill_analytics(self):
        return {"enabled": True, "available": True, "counters": {"route_attempts": 0}}

    def submit_markdown_route_feedback(
        self,
        *,
        user_id: str,
        user_msg: str,
        feedback,
        skill_name: str = "",
        match_kind: str = "",
        note: str = "",
    ):
        raw = str(feedback if feedback is not None else "").strip().lower()
        if raw in {"1", "true", "yes", "positive", "up"}:
            helpful = True
        elif raw in {"0", "false", "no", "negative", "down"}:
            helpful = False
        else:
            return {
                "enabled": True,
                "available": True,
                "recorded": False,
                "message": "invalid_feedback",
            }
        row = {
            "user_id": str(user_id or "").strip().lower(),
            "user_msg": str(user_msg or ""),
            "helpful": bool(helpful),
            "skill_name": str(skill_name or ""),
            "match_kind": str(match_kind or ""),
            "note": str(note or ""),
        }
        self.feedback_calls.append(row)
        return {
            "enabled": True,
            "available": True,
            "recorded": True,
            "helpful": bool(helpful),
            "skill": row["skill_name"] or "systematic-debugging",
            "match_kind": row["match_kind"] or "search",
        }

    def get_markdown_skill_evaluation(self, top_n: int = 12, include_recent: bool = False):
        payload = {
            "enabled": True,
            "available": True,
            "feedback": {
                "total": len(self.feedback_calls),
                "positive": sum(1 for item in self.feedback_calls if bool(item.get("helpful"))),
                "negative": sum(1 for item in self.feedback_calls if not bool(item.get("helpful"))),
                "positive_rate_pct": 100.0,
            },
            "routing": {
                "attempts": 4,
                "hits": 3,
                "hit_rate_pct": 75.0,
                "policy_filtered": 0,
                "policy_blocked": 0,
                "policy_escalated": 0,
            },
            "top_feedback_skills": [{"name": "systematic-debugging", "count": len(self.feedback_calls)}],
            "feedback_by_match_kind": [{"name": "search", "count": len(self.feedback_calls)}],
            "router_tuning": {
                "enabled": True,
                "min_samples": 3,
                "max_boost": 45,
                "max_penalty": 60,
                "match_weight": 0.35,
            },
        }
        if include_recent:
            payload["recent_feedback"] = list(self.feedback_calls)
        _ = top_n
        return payload

    def preview_markdown_route(self, user_msg: str, user_id: str = "default", top_k: int = 5):
        return {
            "enabled": True,
            "available": True,
            "matched": bool(str(user_msg or "").strip()),
            "selected": {
                "name": "systematic-debugging",
                "score": 123,
                "base_score": 87,
                "feedback_adjustment": 20,
                "matched_by": "search",
                "allowed": True,
                "reason": "",
                "trust": "trusted",
            },
            "candidates": [
                {
                    "name": "systematic-debugging",
                    "score": 123,
                    "base_score": 87,
                    "feedback_adjustment": 20,
                    "matched_by": "search",
                    "allowed": True,
                    "trust": "trusted",
                    "reason": "",
                }
            ][: max(1, min(int(top_k or 1), 5))],
            "user_id": str(user_id or "default"),
        }

    def reset_markdown_route_feedback(self):
        previous_total = len(self.feedback_calls)
        self.feedback_calls = []
        return {
            "enabled": True,
            "available": True,
            "reset": True,
            "previous_total": previous_total,
            "message": "feedback reset completed",
        }


class _FakeSkillManager:
    def __init__(self, llm_skill):
        self.intent_map = {"general_conversation": llm_skill}
        self.skills = [llm_skill]


class _DummySkill:
    def __init__(self, manager):
        self.skill_manager = manager


class SkillFeedbackApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_skill_feedback_api_")
        cls._db_path = os.path.join(cls._tmpdir, "skill-feedback.sqlite")
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
        cls.fake_llm_skill = _FakeLLMSkill()
        cls.fake_manager = _FakeSkillManager(cls.fake_llm_skill)
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

    def test_user_can_submit_skill_feedback(self):
        token = self._login_token("user@test.local")
        headers = self._auth_header(token)

        res = self.client.post(
            "/api/skills/feedback",
            headers=headers,
            json={
                "message": "tolong debug issue ini",
                "feedback": "positive",
                "skill_name": "systematic-debugging",
                "match_kind": "search",
            },
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        payload = res.get_json() or {}
        self.assertEqual(payload.get("viewer_scope"), "user")
        self.assertTrue(bool(payload.get("recorded")))
        self.assertTrue(self.fake_llm_skill.feedback_calls)

    def test_feedback_rejects_invalid_value(self):
        token = self._login_token("user@test.local")
        headers = self._auth_header(token)

        res = self.client.post(
            "/api/skills/feedback",
            headers=headers,
            json={
                "message": "tolong debug issue ini",
                "feedback": "maybe",
            },
        )
        self.assertEqual(res.status_code, 400, res.get_data(as_text=True))

    def test_admin_can_view_skill_evaluation(self):
        token = self._login_token("admin@test.local")
        headers = self._auth_header(token)

        res = self.client.get(
            "/api/admin/skills/evaluation?q=debug%20issue&top=7&include_recent=1",
            headers=headers,
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        payload = res.get_json() or {}
        self.assertEqual(payload.get("viewer_scope"), "admin")
        self.assertEqual(7, int(payload.get("top_n", 0)))
        route_preview = payload.get("route_preview") or {}
        self.assertTrue(bool(route_preview.get("matched")))

    def test_admin_can_preview_route_and_reset_feedback(self):
        token = self._login_token("admin@test.local")
        headers = self._auth_header(token)

        preview = self.client.get(
            "/api/admin/skills/route-preview?q=debug%20issue&top_k=4",
            headers=headers,
        )
        self.assertEqual(preview.status_code, 200, preview.get_data(as_text=True))
        preview_payload = preview.get_json() or {}
        self.assertEqual("admin", preview_payload.get("viewer_scope"))
        self.assertTrue(bool(preview_payload.get("matched")))

        reset = self.client.post("/api/admin/skills/feedback/reset", headers=headers, json={})
        self.assertEqual(reset.status_code, 200, reset.get_data(as_text=True))
        reset_payload = reset.get_json() or {}
        self.assertTrue(bool(reset_payload.get("reset")))
        self.assertEqual("admin", reset_payload.get("viewer_scope"))

    def test_non_admin_blocked_from_evaluation(self):
        token = self._login_token("user@test.local")
        headers = self._auth_header(token)
        res = self.client.get("/api/admin/skills/evaluation", headers=headers)
        self.assertEqual(res.status_code, 403, res.get_data(as_text=True))

    def test_non_admin_blocked_from_admin_preview_and_reset(self):
        token = self._login_token("user@test.local")
        headers = self._auth_header(token)
        preview = self.client.get("/api/admin/skills/route-preview?q=debug", headers=headers)
        self.assertEqual(preview.status_code, 403, preview.get_data(as_text=True))
        reset = self.client.post("/api/admin/skills/feedback/reset", headers=headers, json={})
        self.assertEqual(reset.status_code, 403, reset.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

