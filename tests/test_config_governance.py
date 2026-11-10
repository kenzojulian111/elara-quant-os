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
from config.governance import build_config_registry, registry_contract


class ConfigGovernanceTests(unittest.TestCase):
    def tearDown(self):
        settings.Config.reload()

    def _reload_from_temp(
        self,
        *,
        env_example_text: str = "",
        env_text: str = "",
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        env_overrides = dict(env_overrides or {})
        self.tmpdir = tempfile.TemporaryDirectory(prefix="elara_cfg_governance_")
        tmp = Path(self.tmpdir.name)
        env_example_path = tmp / ".env.example"
        env_path = tmp / ".env"
        env_example_path.write_text(env_example_text, encoding="utf-8")
        env_path.write_text(env_text, encoding="utf-8")
        self.patchers = [
            patch.object(settings, "ENV_EXAMPLE_PATH", env_example_path),
            patch.object(settings, "ENV_PATH", env_path),
            patch.dict(os.environ, env_overrides, clear=True),
        ]
        for patcher in self.patchers:
            patcher.start()
        settings.Config.reload()

    def _cleanup_temp(self) -> None:
        for patcher in reversed(getattr(self, "patchers", [])):
            patcher.stop()
        if hasattr(self, "tmpdir"):
            self.tmpdir.cleanup()

    def test_domain_env_layers_between_example_and_dotenv(self):
        with tempfile.TemporaryDirectory(prefix="elara_domain_layer_") as tmpdir:
            domain_file = Path(tmpdir) / "trading.env"
            domain_file.write_text(
                "\n".join(
                    [
                        "CONFIG_PROFILE=paper",
                        "TRADING_SYMBOL=BTC/USDT",
                        "TRADING_STRATEGY=trend",
                    ]
                ),
                encoding="utf-8",
            )
            env_example = "\n".join(
                [
                    "CONFIG_DOMAIN_FILES_ENABLED=1",
                    f"CONFIG_DOMAIN_FILES={domain_file}",
                    "CONFIG_SNAPSHOT_AUDIT_ENABLED=0",
                    "TRADING_SYMBOL=ETH/USDT",
                    "TRADING_STRATEGY=swing",
                    "TRADING_FILE_IPC_FALLBACK_ENABLED=0",
                    "TRADING_STATUS_FILE_FALLBACK_ENABLED=0",
                    "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED=0",
                ]
            )
            self._reload_from_temp(
                env_example_text=env_example,
                env_text="TRADING_SYMBOL=SOL/USDT\n",
            )
            try:
                self.assertEqual("paper", settings.Config.profile_name())
                self.assertEqual("SOL/USDT", settings.Config.TRADING_SYMBOL)
                self.assertEqual("trend", settings.Config.TRADING_STRATEGY)
                self.assertIn(str(domain_file.resolve()), settings.Config.CONFIG_SOURCE_LAYERS)
            finally:
                self._cleanup_temp()

    def test_registry_snapshot_redacts_secret_values(self):
        self._reload_from_temp(
            env_example_text="CONFIG_SNAPSHOT_AUDIT_ENABLED=0\n",
            env_overrides={
                "SECRET_KEY": "real-session-secret",
                "OPENAI_API_KEY": "real-openai-secret",
            },
        )
        try:
            rendered = str(settings.Config.config_registry_snapshot())
            self.assertNotIn("real-session-secret", rendered)
            self.assertNotIn("real-openai-secret", rendered)
            self.assertIn("OPENAI_API_KEY", rendered)
            self.assertIn("SECRET_KEY", rendered)
        finally:
            self._cleanup_temp()

    def test_config_registry_hash_is_stable(self):
        self._reload_from_temp(env_example_text="CONFIG_SNAPSHOT_AUDIT_ENABLED=0\n")
        try:
            self.assertEqual(settings.Config.config_registry_hash(), settings.Config.config_registry_hash())
        finally:
            self._cleanup_temp()

    def test_registry_validation_detects_transport_conflict(self):
        registry = build_config_registry(
            {
                "STATE_BACKEND": "redis",
                "REDIS_EVENT_BUS_REQUIRED": True,
                "STATE_BACKEND_FAIL_FAST": True,
                "TRADING_FILE_IPC_FALLBACK_ENABLED": True,
            }
        )

        self.assertIn("runtime.transport_conflict=redis_required_with_file_fallback", registry.validate().errors)

    def test_registry_validation_detects_experimental_overload(self):
        registry = build_config_registry(
            {
                "ENABLE_STREAM": True,
                "ENABLE_VOICE": True,
                "RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL": 1,
            }
        )

        self.assertIn("experimental.too_many_enabled=2>1", registry.validate().errors)

    def test_strict_unknown_key_can_fail_fast(self):
        with tempfile.TemporaryDirectory(prefix="elara_strict_unknown_") as tmpdir:
            env_example_path = Path(tmpdir) / ".env.example"
            env_path = Path(tmpdir) / ".env"
            env_example_path.write_text("CONFIG_STRICT_UNKNOWN_KEYS=1\n", encoding="utf-8")
            env_path.write_text("TRDAING_IPC_ENABLED=1\n", encoding="utf-8")
            with (
                patch.object(settings, "ENV_EXAMPLE_PATH", env_example_path),
                patch.object(settings, "ENV_PATH", env_path),
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(ValueError, "unknown_config_keys=TRDAING_IPC_ENABLED"):
                    settings.Config.reload()

    def test_deprecated_metadata_is_exposed(self):
        registry = build_config_registry(
            {},
            deprecated_keys=("TG_ALLOWED_USER_ID",),
            deprecated_metadata={"TG_ALLOWED_USER_ID": "Use TG_ALLOWED_USER instead."},
        )

        snapshot = registry.snapshot(redacted=True)
        self.assertEqual("TG_ALLOWED_USER", snapshot["deprecated"]["TG_ALLOWED_USER_ID"]["replacement_key"])

    def test_domain_contract_lists_governance_domains(self):
        contract = registry_contract()

        self.assertIn("runtime", contract)
        self.assertIn("trading", contract)
        self.assertIn("observability", contract)
        self.assertIn("experimental", contract)
        self.assertIn("configured_keys", contract["secrets"])

    def test_config_snapshot_audit_is_written_redacted(self):
        with tempfile.TemporaryDirectory(prefix="elara_config_audit_") as tmpdir:
            audit_path = Path(tmpdir) / "config_snapshots.jsonl"
            self._reload_from_temp(
                env_example_text="\n".join(
                    [
                        "CONFIG_SNAPSHOT_AUDIT_ENABLED=1",
                        f"CONFIG_SNAPSHOT_AUDIT_PATH={audit_path}",
                        "OPENAI_API_KEY=real-openai-secret",
                    ]
                )
            )
            try:
                rendered = audit_path.read_text(encoding="utf-8")
                self.assertIn("elara.config.audit.v1", rendered)
                self.assertNotIn("real-openai-secret", rendered)
            finally:
                self._cleanup_temp()


if __name__ == "__main__":
    unittest.main()
