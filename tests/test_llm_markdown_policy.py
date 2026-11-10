import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from skills.LLMSkill import LLMSkill


class _FakeStateManager:
    def __init__(self):
        self._data = {}

    def set_state(self, key, value, user_id="default", ttl=None):
        _ = ttl
        self._data[(user_id, key)] = value

    def get_state(self, key, user_id="default"):
        return self._data.get((user_id, key))

    def delete_state(self, key, user_id="default"):
        self._data.pop((user_id, key), None)


class _FakeDBManager:
    def __init__(self, roles=None):
        self.roles = {str(k).strip().lower(): str(v).strip().lower() for k, v in (roles or {}).items()}

    def get_user_role(self, identity):
        return self.roles.get(str(identity or "").strip().lower(), "")


class _DummyDoc:
    def __init__(self, name, trust_level="standard", body="normal body"):
        self.name = name
        self.description = f"desc:{name}"
        self.path = f"/tmp/{name}/SKILL.md"
        self.body = body
        self.trust_level = trust_level
        self.tags = []


class LLMSkillMarkdownPolicyTests(unittest.TestCase):
    CFG_KEYS = [
        "LLM_MARKDOWN_SKILLS_ENABLED",
        "LLM_MARKDOWN_POLICY_ENABLED",
        "LLM_MARKDOWN_SKILLS_PROFILE",
        "LLM_MARKDOWN_MIN_TRUST",
        "LLM_MARKDOWN_DEFAULT_TRUST",
        "LLM_MARKDOWN_BLOCKED_SKILLS",
        "LLM_MARKDOWN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_PROFILE_SKILL_RULES",
        "LLM_MARKDOWN_USER_PROFILE_OVERRIDES",
        "LLM_MARKDOWN_ROLE_PROFILE_OVERRIDES",
        "LLM_MARKDOWN_USER_MIN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES",
        "LLM_MARKDOWN_ROLE_BLOCKED_SKILLS_OVERRIDES",
        "LLM_MARKDOWN_USER_ROLE_OVERRIDES",
        "LLM_MARKDOWN_USER_TENANT_OVERRIDES",
        "LLM_MARKDOWN_TENANT_PROFILE_OVERRIDES",
        "LLM_MARKDOWN_TENANT_MIN_TRUST_OVERRIDES",
        "LLM_MARKDOWN_TENANT_BLOCKED_SKILLS_OVERRIDES",
        "LLM_MARKDOWN_ESCALATION_ENABLED",
        "LLM_MARKDOWN_ESCALATION_ROLES",
        "LLM_MARKDOWN_ESCALATION_PHRASES",
        "LLM_MARKDOWN_PROMPT_GUARD_ENABLED",
        "LLM_MARKDOWN_BLOCK_SUSPICIOUS_BODY",
        "LLM_MARKDOWN_ROUTER_ENABLED",
        "LLM_MARKDOWN_SKILLS_DIRS",
    ]

    def setUp(self):
        self._cfg = {key: getattr(Config, key) for key in self.CFG_KEYS}
        Config.LLM_MARKDOWN_SKILLS_ENABLED = True
        Config.LLM_MARKDOWN_POLICY_ENABLED = True
        Config.LLM_MARKDOWN_SKILLS_PROFILE = "elara"
        Config.LLM_MARKDOWN_MIN_TRUST = "restricted"
        Config.LLM_MARKDOWN_DEFAULT_TRUST = "standard"
        Config.LLM_MARKDOWN_BLOCKED_SKILLS = ""
        Config.LLM_MARKDOWN_TRUST_OVERRIDES = ""
        Config.LLM_MARKDOWN_PROFILE_SKILL_RULES = ""
        Config.LLM_MARKDOWN_USER_PROFILE_OVERRIDES = ""
        Config.LLM_MARKDOWN_ROLE_PROFILE_OVERRIDES = ""
        Config.LLM_MARKDOWN_USER_MIN_TRUST_OVERRIDES = ""
        Config.LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES = ""
        Config.LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES = ""
        Config.LLM_MARKDOWN_ROLE_BLOCKED_SKILLS_OVERRIDES = ""
        Config.LLM_MARKDOWN_USER_ROLE_OVERRIDES = ""
        Config.LLM_MARKDOWN_USER_TENANT_OVERRIDES = ""
        Config.LLM_MARKDOWN_TENANT_PROFILE_OVERRIDES = ""
        Config.LLM_MARKDOWN_TENANT_MIN_TRUST_OVERRIDES = ""
        Config.LLM_MARKDOWN_TENANT_BLOCKED_SKILLS_OVERRIDES = ""
        Config.LLM_MARKDOWN_ESCALATION_ENABLED = True
        Config.LLM_MARKDOWN_ESCALATION_ROLES = "admin,superuser"
        Config.LLM_MARKDOWN_ESCALATION_PHRASES = "force skill,override policy"
        Config.LLM_MARKDOWN_PROMPT_GUARD_ENABLED = True
        Config.LLM_MARKDOWN_BLOCK_SUSPICIOUS_BODY = True
        Config.LLM_MARKDOWN_ROUTER_ENABLED = True
        Config.LLM_MARKDOWN_SKILLS_DIRS = ""

    def tearDown(self):
        for key, value in self._cfg.items():
            setattr(Config, key, value)

    def _make_skill(self, db_manager=None):
        return LLMSkill(
            state_manager=_FakeStateManager(),
            primary_provider="groq",
            db_manager=db_manager,
        )

    def test_user_profile_override_blocks_skill_not_in_profile_rules(self):
        Config.LLM_MARKDOWN_PROFILE_SKILL_RULES = "safe=systematic-debugging,elara=elara-trading-ops|systematic-debugging"
        Config.LLM_MARKDOWN_USER_PROFILE_OVERRIDES = "alice@example.com=safe"
        skill = self._make_skill()
        doc = _DummyDoc(name="elara-trading-ops", trust_level="trusted")
        skill._route_markdown_skill = lambda _msg: {"doc": doc, "score": 99, "matched_by": "explicit"}

        ctx_alice, info_alice = skill._build_markdown_skill_context("pakai elara-trading-ops", user_id="alice@example.com")
        self.assertEqual("", ctx_alice)
        self.assertFalse(bool(info_alice.get("allowed")))
        self.assertIn("profile", str(info_alice.get("reason", "")).lower())

        ctx_bob, info_bob = skill._build_markdown_skill_context("pakai elara-trading-ops", user_id="bob@example.com")
        self.assertTrue(bool(info_bob.get("allowed")))
        self.assertIn("My-skill aktif", ctx_bob)

    def test_role_min_trust_override_blocks_low_trust_doc(self):
        Config.LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES = "admin=trusted"
        Config.LLM_MARKDOWN_USER_ROLE_OVERRIDES = "eve@example.com=admin"
        skill = self._make_skill()
        doc = _DummyDoc(name="systematic-debugging", trust_level="standard")
        skill._route_markdown_skill = lambda _msg: {"doc": doc, "score": 95, "matched_by": "search"}

        ctx, info = skill._build_markdown_skill_context("debug issue", user_id="eve@example.com")
        self.assertEqual("", ctx)
        self.assertFalse(bool(info.get("allowed")))
        self.assertIn("di bawah minimum", str(info.get("reason", "")).lower())

    def test_user_blocked_skill_override_enforced(self):
        Config.LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES = "bob@example.com=quant-analyst|risk-manager"
        skill = self._make_skill()
        doc = _DummyDoc(name="quant-analyst", trust_level="trusted")
        skill._route_markdown_skill = lambda _msg: {"doc": doc, "score": 90, "matched_by": "explicit"}

        ctx, info = skill._build_markdown_skill_context("pakai quant-analyst", user_id="bob@example.com")
        self.assertEqual("", ctx)
        self.assertFalse(bool(info.get("allowed")))
        self.assertIn("diblokir", str(info.get("reason", "")).lower())

    def test_policy_meta_command_returns_effective_user_context(self):
        Config.LLM_MARKDOWN_USER_PROFILE_OVERRIDES = "alice@example.com=safe"
        Config.LLM_MARKDOWN_USER_MIN_TRUST_OVERRIDES = "alice@example.com=trusted"
        Config.LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES = "alice@example.com=quant-analyst|risk-manager"
        skill = self._make_skill()

        output = skill._handle_markdown_skill_meta_command("/skills policy", user_id="alice@example.com")
        low = output.lower()
        self.assertIn("identity: alice@example.com", low)
        self.assertIn("profile: safe", low)
        self.assertIn("min_trust: trusted", low)
        self.assertIn("blocked_skills: 2", low)

    def test_role_lookup_from_database_applies_profile_override(self):
        Config.LLM_MARKDOWN_ROLE_PROFILE_OVERRIDES = "admin=safe"
        db_manager = _FakeDBManager(roles={"dbuser@example.com": "admin"})
        skill = self._make_skill(db_manager=db_manager)

        policy = skill._resolve_user_policy_context("dbuser@example.com")
        self.assertEqual("admin", str(policy.get("role")))
        self.assertEqual("safe", str(policy.get("profile")))

    def test_sources_meta_command_reports_loaded_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "elara-trading-ops")
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\n"
                    "name: elara-trading-ops\n"
                    "description: test\n"
                    "trust_level: trusted\n"
                    "---\n\n"
                    "Body\n"
                )

            Config.LLM_MARKDOWN_SKILLS_DIRS = tmp
            skill = self._make_skill()
            output = skill._handle_markdown_skill_meta_command("/skills sources", user_id="alice@example.com")
            low = output.lower()

            self.assertIn("sumber my-skill", low)
            self.assertIn("loaded=1", low)
            self.assertIn(str(tmp).lower(), low)

    def test_tenant_profile_override_blocks_skill_not_allowed_in_tenant_profile(self):
        Config.LLM_MARKDOWN_PROFILE_SKILL_RULES = "safe=systematic-debugging,elara=elara-trading-ops|systematic-debugging"
        Config.LLM_MARKDOWN_USER_TENANT_OVERRIDES = "alice@example.com=tenant-acme"
        Config.LLM_MARKDOWN_TENANT_PROFILE_OVERRIDES = "tenant-acme=safe"
        skill = self._make_skill()
        doc = _DummyDoc(name="elara-trading-ops", trust_level="trusted")
        skill._route_markdown_skill = lambda _msg, **_kwargs: {"doc": doc, "score": 98, "matched_by": "explicit"}

        ctx, info = skill._build_markdown_skill_context("pakai elara-trading-ops", user_id="alice@example.com")
        self.assertEqual("", ctx)
        self.assertFalse(bool(info.get("allowed")))
        self.assertEqual("tenant-acme", str(info.get("policy_tenant")))
        self.assertIn("profile", str(info.get("reason", "")).lower())

    def test_escalation_allows_admin_bypass_trust_policy(self):
        Config.LLM_MARKDOWN_USER_ROLE_OVERRIDES = "admin@example.com=admin"
        Config.LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES = "admin=trusted"
        Config.LLM_MARKDOWN_ESCALATION_ENABLED = True
        Config.LLM_MARKDOWN_ESCALATION_ROLES = "admin"
        Config.LLM_MARKDOWN_ESCALATION_PHRASES = "force skill"
        skill = self._make_skill()
        doc = _DummyDoc(name="systematic-debugging", trust_level="standard")
        skill._route_markdown_skill = lambda _msg, **_kwargs: {"doc": doc, "score": 97, "matched_by": "explicit"}

        ctx, info = skill._build_markdown_skill_context(
            "force skill pakai systematic-debugging",
            user_id="admin@example.com",
        )
        self.assertIn("My-skill aktif", ctx)
        self.assertTrue(bool(info.get("allowed")))
        self.assertTrue(bool(info.get("escalated")))
        self.assertIn("escalated", str(info.get("reason", "")).lower())

    def test_feedback_and_evaluation_summary(self):
        skill = self._make_skill()
        doc = _DummyDoc(name="systematic-debugging", trust_level="trusted")
        skill._route_markdown_skill = lambda _msg, **_kwargs: {"doc": doc, "score": 95, "matched_by": "search"}

        pos = skill.submit_markdown_route_feedback(
            user_id="alice@example.com",
            user_msg="debug issue now",
            feedback="positive",
            note="helpful route",
        )
        neg = skill.submit_markdown_route_feedback(
            user_id="alice@example.com",
            user_msg="debug issue now",
            feedback="negative",
            note="less relevant answer",
        )

        self.assertTrue(bool(pos.get("recorded")))
        self.assertTrue(bool(neg.get("recorded")))

        report = skill.get_markdown_skill_evaluation(top_n=5, include_recent=True)
        feedback = report.get("feedback") or {}
        self.assertEqual(2, int(feedback.get("total", 0)))
        self.assertEqual(1, int(feedback.get("positive", 0)))
        self.assertEqual(1, int(feedback.get("negative", 0)))
        self.assertTrue(bool(report.get("recent_feedback")))

    def test_feedback_adaptive_router_reorders_candidates(self):
        skill = self._make_skill()
        doc_a = _DummyDoc(name="quant-analyst", trust_level="trusted")
        doc_b = _DummyDoc(name="risk-manager", trust_level="trusted")

        class _Registry:
            def search_scored(self, _query, limit=8):
                _ = limit
                return [(doc_a, 100), (doc_b, 100)]

            def route_from_text(self, *_args, **_kwargs):
                return None

            def resolve_from_text(self, *_args, **_kwargs):
                return None

        skill.set_markdown_registry(_Registry())

        first = skill._route_markdown_skill("xqzv ptnr", user_id="alice@example.com", include_candidates=True)
        self.assertIsNotNone(first)
        self.assertEqual("quant-analyst", str(getattr(first.get("doc"), "name", "")).lower())

        for _ in range(4):
            skill.submit_markdown_route_feedback(
                user_id="alice@example.com",
                user_msg="xqzv ptnr",
                feedback="negative",
                skill_name="quant-analyst",
                match_kind="search",
            )
            skill.submit_markdown_route_feedback(
                user_id="alice@example.com",
                user_msg="xqzv ptnr",
                feedback="positive",
                skill_name="risk-manager",
                match_kind="search",
            )

        second = skill._route_markdown_skill("xqzv ptnr", user_id="alice@example.com", include_candidates=True)
        self.assertIsNotNone(second)
        self.assertEqual("risk-manager", str(getattr(second.get("doc"), "name", "")).lower())
        self.assertGreater(int(second.get("feedback_adjustment") or 0), 0)

    def test_feedback_reset_clears_adaptive_signal(self):
        skill = self._make_skill()
        for _ in range(3):
            skill.submit_markdown_route_feedback(
                user_id="alice@example.com",
                user_msg="debug issue",
                feedback="positive",
                skill_name="systematic-debugging",
                match_kind="search",
            )

        before = skill.get_markdown_skill_evaluation(top_n=5, include_recent=True)
        self.assertEqual(3, int((before.get("feedback") or {}).get("total", 0)))

        reset_payload = skill.reset_markdown_route_feedback()
        self.assertTrue(bool(reset_payload.get("reset")))
        self.assertEqual(3, int(reset_payload.get("previous_total", 0)))

        after = skill.get_markdown_skill_evaluation(top_n=5, include_recent=True)
        self.assertEqual(0, int((after.get("feedback") or {}).get("total", 0))
        )
        self.assertFalse(bool(after.get("recent_feedback")))


if __name__ == "__main__":
    unittest.main()

