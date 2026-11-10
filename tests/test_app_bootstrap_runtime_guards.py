import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import app_bootstrap
from core.events import core_heartbeat_key


class AppBootstrapRuntimeGuardTests(unittest.TestCase):
    def _patch_runtime_baseline(self):
        stack = ExitStack()
        stack.enter_context(patch.object(app_bootstrap, "_is_production_like_env", return_value=False))
        stack.enter_context(patch.object(app_bootstrap, "_is_live_trading_mode", return_value=False))
        stack.enter_context(patch.object(app_bootstrap.Config, "SECRET_KEY", "unit-strong-secret-1234567890"))
        stack.enter_context(patch.object(app_bootstrap.Config, "JWT_SECRET_KEY", "unit-strong-jwt-1234567890"))
        stack.enter_context(patch.object(app_bootstrap.Config, "ALLOW_DEV_BACKDOOR", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "DEBUG", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "REDIS_EVENT_BUS_REQUIRED", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "CORS_ORIGINS", ""))
        stack.enter_context(patch.object(app_bootstrap.Config, "WEB_REVERSE_PROXY_ENABLED", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "WEB_REVERSE_PROXY_REQUIRED_IN_PROD", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "WEB_REVERSE_PROXY_HOPS", 1))
        stack.enter_context(patch.object(app_bootstrap.Config, "SQLALCHEMY_DATABASE_URI", "sqlite:///unit-test.db"))
        stack.enter_context(patch.object(app_bootstrap.Config, "DB_ALLOW_SQLITE", True))
        stack.enter_context(patch.object(app_bootstrap.Config, "DB_REQUIRE_SERVER_FOR_PROD", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "DB_REQUIRE_SERVER_FOR_LIVE_TRADING", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "STATE_BACKEND", "auto"))
        stack.enter_context(patch.object(app_bootstrap.Config, "STATE_BACKEND_FAIL_FAST", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "STATE_REQUIRE_REDIS_FOR_PROD", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "STATE_REQUIRE_REDIS_FOR_LIVE_TRADING", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "STATE_ENCRYPTION_ENABLED", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "STATE_ENCRYPTION_REQUIRED", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "FAIL_FAST_STARTUP", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "REQUIRED_COMPONENTS", []))
        stack.enter_context(patch.object(app_bootstrap.Config, "LLM_AGENT_ORCHESTRATOR_ENABLED", False))
        stack.enter_context(patch.object(app_bootstrap.Config, "LLM_NLU_PROVIDERS", "groq,gemini,openai"))
        return stack

    def test_parse_web_proxy_hops_accepts_positive_int(self):
        self.assertEqual(app_bootstrap._parse_web_proxy_hops("2"), 2)

    def test_parse_web_proxy_hops_rejects_zero(self):
        with self.assertRaises(ValueError):
            app_bootstrap._parse_web_proxy_hops(0)

    def test_parse_web_proxy_hops_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            app_bootstrap._parse_web_proxy_hops("abc")

    def test_resolve_core_heartbeat_key_falls_back_when_blank(self):
        with patch.object(app_bootstrap.Config, "CORE_HEARTBEAT_KEY", ""):
            key = app_bootstrap._resolve_core_heartbeat_key("elara:test:")
        self.assertEqual(key, core_heartbeat_key("elara:test:"))

    def test_resolve_core_heartbeat_path_uses_default_when_blank_and_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(app_bootstrap.Config, "CORE_HEARTBEAT_PATH", ""), \
             patch.object(app_bootstrap.Config, "root_path", tmpdir):
            path = app_bootstrap._resolve_core_heartbeat_path(True)
        self.assertEqual(path, os.path.join(tmpdir, "runtime", "core_heartbeat.json"))

    def test_validate_runtime_config_allows_missing_primary_llm_key_in_dev(self):
        with self._patch_runtime_baseline() as stack:
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_PRIMARY_PROVIDER", "groq"))
            stack.enter_context(patch.object(app_bootstrap.Config, "GROQ_API_KEY", ""))
            stack.enter_context(patch.object(app_bootstrap.Config, "OPENAI_API_KEY", ""))
            stack.enter_context(patch.object(app_bootstrap.Config, "GEMINI_API_KEY", ""))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_ALLOW_NO_API_KEY_IN_DEV", True))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_REQUIRE_PRIMARY_API_KEY", False))
            app_bootstrap._validate_runtime_config()

    def test_validate_runtime_config_blocks_missing_primary_llm_key_when_required(self):
        with self._patch_runtime_baseline() as stack:
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_PRIMARY_PROVIDER", "groq"))
            stack.enter_context(patch.object(app_bootstrap.Config, "GROQ_API_KEY", ""))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_ALLOW_NO_API_KEY_IN_DEV", False))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_REQUIRE_PRIMARY_API_KEY", True))
            with self.assertRaises(SystemExit):
                app_bootstrap._validate_runtime_config()

    def test_validate_runtime_config_blocks_failed_redis_readiness(self):
        with self._patch_runtime_baseline() as stack:
            stack.enter_context(patch.object(app_bootstrap.Config, "REDIS_EVENT_BUS_REQUIRED", True))
            stack.enter_context(patch.object(app_bootstrap.Config, "REDIS_URL", "redis://unit"))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_PRIMARY_PROVIDER", "groq"))
            stack.enter_context(patch.object(app_bootstrap.Config, "GROQ_API_KEY", ""))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_ALLOW_NO_API_KEY_IN_DEV", True))
            stack.enter_context(patch.object(app_bootstrap.Config, "LLM_REQUIRE_PRIMARY_API_KEY", False))
            stack.enter_context(patch.object(app_bootstrap, "get_redis", return_value=object()))
            stack.enter_context(patch.object(app_bootstrap, "redis_readiness_probe", return_value={"ok": False}))
            with self.assertRaises(SystemExit):
                app_bootstrap._validate_runtime_config()

    def test_build_mqtt_service_returns_dummy_when_not_requested(self):
        with patch.object(app_bootstrap.Config, "REQUIRED_COMPONENTS", []):
            service = app_bootstrap._build_mqtt_service({}, None, None)
        self.assertIsInstance(service, app_bootstrap.DummyMQTT)

    def test_start_interfaces_skips_presence_when_disabled(self):
        def fake_loader(module_path, attr_name, *_args, **_kwargs):
            if module_path == "runtime.interfaces.presence_service" and attr_name == "PresenceService":
                class PresenceService:
                    def __init__(self, *_a, **_kw):
                        pass

                    def start(self):
                        raise AssertionError("presence should stay disabled")

                return PresenceService
            return None

        with patch.object(app_bootstrap, "_load_optional_attr", side_effect=fake_loader), \
             patch.object(app_bootstrap.Config, "PRESENCE_ENABLED", False), \
             patch.object(app_bootstrap.Config, "ENABLE_STREAM", False), \
             patch.object(app_bootstrap.Config, "ENABLE_VOICE", False), \
             patch.object(app_bootstrap.Config, "REQUIRED_COMPONENTS", []), \
             patch.object(app_bootstrap.Config, "HEALTH_POLL_PATH", ""):
            _app, _socketio, services = app_bootstrap._start_interfaces(
                elara=object(),
                state_manager=None,
                db_manager=None,
                life_manager=None,
                mqtt_service=app_bootstrap.DummyMQTT(),
                self_heal_service=None,
            )
        names = [name for name, _svc in services]
        self.assertNotIn("presence", names)


if __name__ == "__main__":
    unittest.main()

