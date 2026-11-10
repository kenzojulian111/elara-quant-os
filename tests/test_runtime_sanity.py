import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.runtime_sanity import evaluate_runtime_sanity


def _config(**overrides):
    base = {
        "TRADING_MODE": "paper",
        "RUNTIME_EVENT_STORE_ENABLED": True,
        "RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL": 1,
        "EXPERIMENTAL_RUNTIME_ALLOWLIST": "",
        "ENABLE_VOICE": False,
        "ENABLE_STREAM": False,
        "PRESENCE_ENABLED": False,
        "ENABLE_KNOWLEDGE_SKILL": False,
        "LLM_MARKDOWN_SKILLS_ENABLED": False,
        "DB_AUTO_CREATE_TABLES": False,
        "REDIS_EVENT_BUS_REQUIRED": False,
        "TRADING_FILE_IPC_FALLBACK_ENABLED": False,
        "TRADING_STATUS_FILE_FALLBACK_ENABLED": False,
        "CORE_HEARTBEAT_FILE_FALLBACK_ENABLED": False,
    }
    base.update(overrides)

    def config_hash():
        return "hash-test"

    def unknown_config_keys():
        return tuple(base.pop("_unknown", ()))

    def deprecated_config_keys():
        return tuple(base.pop("_deprecated", ()))

    return types.SimpleNamespace(
        **base,
        config_hash=config_hash,
        unknown_config_keys=unknown_config_keys,
        deprecated_config_keys=deprecated_config_keys,
    )


class RuntimeSanityTests(unittest.TestCase):
    def test_clean_runtime_reports_ok(self):
        report = evaluate_runtime_sanity(config=_config())

        self.assertEqual("ok", report.status)
        self.assertEqual(100, report.score)

    def test_experimental_complexity_budget_warns(self):
        report = evaluate_runtime_sanity(
            config=_config(
                EXPERIMENTAL_RUNTIME_ALLOWLIST="voice,stream",
                ENABLE_VOICE=True,
                ENABLE_STREAM=True,
                RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL=1,
            )
        )

        self.assertEqual("warning", report.status)
        self.assertIn("experimental_complexity_budget_exceeded", [finding.code for finding in report.findings])

    def test_full_runtime_with_exchange_down_is_critical(self):
        report = evaluate_runtime_sanity(
            config=_config(),
            dependency_states={"exchange": "down"},
            runtime_state="full",
        )

        self.assertEqual("critical", report.status)
        self.assertIn("impossible_full_runtime_state", [finding.code for finding in report.findings])

    def test_live_without_event_store_is_critical(self):
        report = evaluate_runtime_sanity(
            config=_config(TRADING_MODE="live", RUNTIME_EVENT_STORE_ENABLED=False)
        )

        self.assertEqual("critical", report.status)
        self.assertIn("runtime_event_store_disabled", [finding.code for finding in report.findings])

    def test_unknown_and_deprecated_config_are_reported(self):
        report = evaluate_runtime_sanity(config=_config(_unknown=("TRDAING_MODE",), _deprecated=("OLD_KEY",)))

        self.assertEqual("warning", report.status)
        self.assertIn("unknown_config_keys", [finding.code for finding in report.findings])
        self.assertIn("deprecated_config_keys", [finding.code for finding in report.findings])


if __name__ == "__main__":
    unittest.main()
