import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config.settings as settings


class ConfigProfileTests(unittest.TestCase):
    def tearDown(self):
        settings.Config.reload()

    def _snapshot(self, env_overrides: dict[str, str]):
        return self._snapshot_from_files(env_overrides=env_overrides)

    def _snapshot_from_files(
        self,
        *,
        env_overrides: dict[str, str] | None = None,
        env_example_text: str = "",
        env_text: str = "",
    ):
        env_overrides = dict(env_overrides or {})
        with tempfile.TemporaryDirectory(prefix="elara_cfg_profile_") as tmpdir:
            env_example_path = Path(tmpdir) / ".env.example"
            env_path = Path(tmpdir) / ".env"
            env_example_path.write_text(env_example_text, encoding="utf-8")
            env_path.write_text(env_text, encoding="utf-8")
            with (
                patch.object(settings, "ENV_EXAMPLE_PATH", env_example_path),
                patch.object(settings, "ENV_PATH", env_path),
                patch.dict(os.environ, env_overrides, clear=True),
            ):
                settings.Config.reload()
                return {
                    "profile": settings.Config.profile_name(),
                    "identity": settings.Config.identity_summary(),
                    "state_backend": settings.Config.STATE_BACKEND,
                    "redis_required": settings.Config.REDIS_EVENT_BUS_REQUIRED,
                    "trading_mode": settings.Config.TRADING_MODE,
                    "profile_defaults": settings.Config.profile_defaults(),
                    "core_config": settings.Config.core_config(),
                    "advanced_config": settings.Config.advanced_config(),
                    "grouped_config": settings.Config.grouped_config(),
                    "grouped_config_v1": settings.Config.grouped_config_v1(),
                    "unknown_config_keys": settings.Config.unknown_config_keys(),
                    "deprecated_config_keys": settings.Config.deprecated_config_keys(),
                    "experimental_flags": {
                        "ENABLE_KNOWLEDGE_SKILL": getattr(settings.Config, "ENABLE_KNOWLEDGE_SKILL", False),
                        "ENABLE_STREAM": getattr(settings.Config, "ENABLE_STREAM", False),
                        "ENABLE_VOICE": getattr(settings.Config, "ENABLE_VOICE", False),
                        "PRESENCE_ENABLED": getattr(settings.Config, "PRESENCE_ENABLED", False),
                        "LLM_MARKDOWN_SKILLS_ENABLED": getattr(
                            settings.Config, "LLM_MARKDOWN_SKILLS_ENABLED", False
                        ),
                        "LLM_MARKDOWN_POLICY_ENABLED": getattr(
                            settings.Config, "LLM_MARKDOWN_POLICY_ENABLED", False
                        ),
                        "LLM_MARKDOWN_ROUTER_ENABLED": getattr(
                            settings.Config, "LLM_MARKDOWN_ROUTER_ENABLED", False
                        ),
                        "LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED": getattr(
                            settings.Config, "LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED", False
                        ),
                        "LLM_MARKDOWN_SKILLS_WATCH_ENABLED": getattr(
                            settings.Config, "LLM_MARKDOWN_SKILLS_WATCH_ENABLED", False
                        ),
                        "LLM_MARKDOWN_ESCALATION_ENABLED": getattr(
                            settings.Config, "LLM_MARKDOWN_ESCALATION_ENABLED", False
                        ),
                    },
                }

    def test_backtest_profile_applies_decision_platform_defaults(self):
        snapshot = self._snapshot({"CONFIG_PROFILE": "backtest"})

        self.assertEqual("backtest", snapshot["profile"])
        self.assertEqual("file", snapshot["state_backend"])
        self.assertFalse(snapshot["redis_required"])
        self.assertEqual("backtest", snapshot["trading_mode"])
        self.assertIn("trading decision platform", snapshot["identity"].lower())
        self.assertEqual("backtest", snapshot["profile_defaults"]["TRADING_MODE"])

    def test_explicit_env_override_wins_over_profile_default(self):
        snapshot = self._snapshot(
            {
                "CONFIG_PROFILE": "backtest",
                "STATE_BACKEND": "redis",
                "REDIS_EVENT_BUS_REQUIRED": "1",
                "TRADING_FILE_IPC_FALLBACK_ENABLED": "0",
                "TRADING_STATUS_FILE_FALLBACK_ENABLED": "0",
                "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED": "0",
            }
        )

        self.assertEqual("backtest", snapshot["profile"])
        self.assertEqual("redis", snapshot["state_backend"])
        self.assertTrue(snapshot["redis_required"])

    def test_redis_required_rejects_file_transport_fallbacks(self):
        with self.assertRaisesRegex(ValueError, "REDIS_EVENT_BUS_REQUIRED=1"):
            self._snapshot(
                {
                    "CONFIG_PROFILE": "backtest",
                    "REDIS_EVENT_BUS_REQUIRED": "1",
                    "TRADING_FILE_IPC_FALLBACK_ENABLED": "1",
                }
            )

    def test_config_layers_expose_meaningful_core_surface(self):
        snapshot = self._snapshot({"CONFIG_PROFILE": "development"})

        self.assertIn("TRADING_MODE", snapshot["core_config"])
        self.assertIn("DATABASE_URL", snapshot["core_config"])
        self.assertNotIn("TRADING_MODE", snapshot["advanced_config"])

    def test_unknown_project_env_key_is_reported(self):
        snapshot = self._snapshot_from_files(env_text="TRDAING_IPC_ENABLED=1\n")

        self.assertEqual(("TRDAING_IPC_ENABLED",), snapshot["unknown_config_keys"])

    def test_deprecated_project_env_key_is_reported(self):
        snapshot = self._snapshot_from_files(env_text="TG_ALLOWED_USER_ID=123\n")

        self.assertEqual(("TG_ALLOWED_USER_ID",), snapshot["deprecated_config_keys"])

    def test_config_snapshot_hash_is_stable_and_redacted(self):
        first = self._snapshot_from_files(
            env_overrides={
                "CONFIG_PROFILE": "paper",
                "TRADING_STRATEGY": "breakout",
                "TRADING_STRATEGY_VERSION": "v2",
                "ENABLE_TRADING_SKILL": "1",
                "OPENAI_API_KEY": "secret-one",
            }
        )
        first_snapshot = settings.Config.deterministic_config_snapshot()
        first_hash = settings.Config.config_hash()
        second_hash = settings.Config.config_hash()

        self.assertEqual(first_hash, second_hash)
        self.assertEqual("v2", first_snapshot["strategy_version"])
        self.assertNotIn("secret-one", str(first_snapshot))
        self.assertIn("ENABLE_TRADING_SKILL", first_snapshot["feature_flags"])
        self.assertEqual("paper", first["profile"])

    def test_profile_transport_defaults_are_explicit(self):
        development = self._snapshot({"CONFIG_PROFILE": "development"})
        backtest = self._snapshot({"CONFIG_PROFILE": "backtest"})
        paper = self._snapshot({"CONFIG_PROFILE": "paper"})

        self.assertTrue(bool(development["profile_defaults"]["TRADING_FILE_IPC_FALLBACK_ENABLED"]))
        self.assertTrue(bool(backtest["profile_defaults"]["CORE_HEARTBEAT_FILE_FALLBACK_ENABLED"]))
        self.assertFalse(bool(paper["profile_defaults"]["TRADING_FILE_IPC_FALLBACK_ENABLED"]))
        self.assertFalse(bool(paper["profile_defaults"]["STATE_ALLOW_MEMORY_FALLBACK"]))

    def test_experimental_surfaces_are_opt_in_by_default(self):
        snapshot = self._snapshot({"CONFIG_PROFILE": "development"})

        for key, value in snapshot["experimental_flags"].items():
            self.assertFalse(bool(value), key)

    def test_grouped_config_schema_exposes_domain_views(self):
        snapshot = self._snapshot(
            {
                "CONFIG_PROFILE": "paper",
                "TRADING_SYMBOL": "BTC/USDT",
                "TRADING_STRATEGY": "trend_following",
                "WEB_ALLOWED_HOSTS": "localhost,127.0.0.1",
                "CORS_ORIGINS": "http://localhost:3000",
                "LLM_PRIMARY_PROVIDER": "groq",
                "LLM_NLU_PROVIDERS": "groq,openai",
                "GROQ_API_KEY": "real-groq-key",
                "OPENAI_API_KEY": "replace-with-openai",
            }
        )

        grouped = snapshot["grouped_config"]
        self.assertEqual("paper", grouped["trading"]["profile"])
        self.assertEqual("BTC/USDT", grouped["trading"]["symbol"])
        self.assertEqual("trend_following", grouped["trading"]["strategy"])
        self.assertEqual("redis", grouped["infra"]["state_backend"])
        self.assertFalse(grouped["infra"]["file_fallback_enabled"])
        self.assertEqual(("localhost", "127.0.0.1"), tuple(grouped["security"]["web_allowed_hosts"]))
        self.assertEqual(("http://localhost:3000",), tuple(grouped["security"]["cors_origins"]))
        self.assertEqual("groq", grouped["llm"]["primary_provider"])
        self.assertEqual(("groq", "openai"), tuple(grouped["llm"]["nlu_providers"]))
        self.assertTrue(grouped["llm"]["groq_configured"])
        self.assertFalse(grouped["llm"]["openai_configured"])

    def test_grouped_config_v1_contract_is_stable_and_versioned(self):
        snapshot = self._snapshot({"CONFIG_PROFILE": "development"})

        grouped_v1 = snapshot["grouped_config_v1"]
        self.assertEqual("v1", grouped_v1["version"])
        self.assertEqual(snapshot["identity"], grouped_v1["identity"])
        self.assertEqual(
            (
                "profile",
                "mode",
                "symbol",
                "strategy",
                "daily_max_loss_pct",
                "max_drawdown_pct",
                "life_min_sleep_hours",
                "life_max_stress",
                "trading_skill_enabled",
            ),
            tuple(grouped_v1["contract"]["trading"]),
        )
        self.assertEqual(
            (
                "environment",
                "state_backend",
                "redis_event_bus_required",
                "state_backend_fail_fast",
                "database_configured",
                "redis_configured",
                "db_auto_create_tables",
                "trading_status_path",
                "trading_command_path",
                "core_heartbeat_path",
                "file_fallback_enabled",
            ),
            tuple(grouped_v1["contract"]["infra"]),
        )
        self.assertEqual(
            (
                "secret_key_configured",
                "jwt_secret_configured",
                "fail_fast_startup",
                "web_force_https",
                "web_reverse_proxy_enabled",
                "web_allowed_hosts",
                "cors_origins",
                "csrf_protect",
                "security_headers",
                "jwt_cookie_csrf_protect",
            ),
            tuple(grouped_v1["contract"]["security"]),
        )
        self.assertEqual(
            (
                "primary_provider",
                "nlu_providers",
                "openai_configured",
                "groq_configured",
                "gemini_configured",
                "allow_no_api_key_in_dev",
            ),
            tuple(grouped_v1["contract"]["llm"]),
        )
        self.assertEqual(snapshot["grouped_config"], grouped_v1["config"])


if __name__ == "__main__":
    unittest.main()

