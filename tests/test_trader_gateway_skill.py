import asyncio
import json
import tempfile
import unittest

from config.settings import Config
from core.events import trade_command_queue_key, trader_status_key
from core.base import NLPUnderstanding
from core.life import PermissionDecision
from skills.TraderGatewaySkill import TraderGatewaySkill


class _FakeLifeManager:
    def __init__(self, allowed: bool, reason: str):
        self.allowed = allowed
        self.reason = reason

    def evaluate_trading(self, user_id="default"):
        return PermissionDecision(self.allowed, self.reason)


def _nlu(intent: str) -> NLPUnderstanding:
    return NLPUnderstanding(intent=intent, confidence=1.0, entities=[], raw_text=intent)


class _FakeHTTPResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeHTTPClient:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response
        self.exc = exc

    async def get(self, url, **_kwargs):
        _ = url
        if self.exc:
            raise self.exc
        return self.response


class TraderGatewaySkillTests(unittest.TestCase):
    def test_trading_status_online(self):
        skill = TraderGatewaySkill()
        skill._load_daemon_status = lambda: {
            "is_running": True,
            "simulation_fallback": False,
            "control_state": "active",
            "life_blocked": False,
            "global_risk_blocked": False,
            "daily_drawdown_pct": 1.2,
            "global_drawdown_pct": 2.5,
            "lite_mode": False,
            "ram_usage_pct": 21.0,
            "open_positions": 2,
            "equity": 1234.5,
            "cash": 456.7,
        }
        result = asyncio.run(skill.handle(_nlu("trading_status")))
        self.assertIn("ONLINE", result)
        self.assertIn("Control active", result)
        self.assertIn("Open positions 2", result)

    def test_trading_start_denied_by_life_gate(self):
        skill = TraderGatewaySkill(life_manager=_FakeLifeManager(False, "Tidur kurang."))
        skill._load_daemon_status = lambda: {"is_running": True}
        result = asyncio.run(skill.handle(_nlu("trading_start")))
        self.assertIn("Trading ditolak", result)

    def test_trading_stop_send_command(self):
        skill = TraderGatewaySkill()
        captured = {"cmd": None}

        def _send(cmd: str, source: str = "core"):
            captured["cmd"] = cmd
            return True

        skill._send_daemon_command = _send
        result = asyncio.run(skill.handle(_nlu("trading_stop")))
        self.assertIn("Perintah stop", result)
        self.assertEqual(captured["cmd"], "stop")

    def test_hedge_fund_brief_offline_when_no_status(self):
        skill = TraderGatewaySkill()
        skill._load_daemon_status = lambda: {}
        result = asyncio.run(skill.handle(_nlu("hedge_fund_brief")))
        self.assertIn("OFFLINE", result)

    def test_hedge_fund_brief_simulation_suffix_not_duplicated(self):
        skill = TraderGatewaySkill()
        skill._load_daemon_status = lambda: {
            "is_running": True,
            "simulation_fallback": True,
            "control_state": "blocked",
            "life_blocked": False,
            "daily_risk_blocked": False,
            "global_risk_blocked": False,
            "daily_drawdown_pct": 1.0,
            "global_drawdown_pct": 2.0,
            "max_drawdown_limit_pct": 10.0,
            "lite_mode": False,
            "ram_usage_pct": 25.0,
            "open_positions": 1,
            "equity": 1000.0,
        }
        result = asyncio.run(skill.handle(_nlu("hedge_fund_brief")))
        self.assertEqual(result.count("(SIMULATION FALLBACK)"), 1)
        self.assertIn("Control State: blocked", result)

    def test_default_redis_keys_follow_runtime_event_keys(self):
        skill = TraderGatewaySkill(redis_client=None)
        redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
        expected_status = str(
            getattr(Config, "TRADING_STATUS_KEY", trader_status_key(redis_prefix))
            or trader_status_key(redis_prefix)
        ).strip()
        expected_command = str(
            getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
            or trade_command_queue_key(redis_prefix)
        ).strip()
        self.assertEqual(skill.status_key, expected_status)
        self.assertEqual(skill.command_key, expected_command)

    def test_load_status_from_http_applies_contract_defaults(self):
        skill = TraderGatewaySkill(
            http_client=_FakeHTTPClient(response=_FakeHTTPResponse({"is_running": 1}))
        )
        skill.api_base = "http://trader"

        payload = asyncio.run(skill._load_status_from_http())

        self.assertTrue(payload["is_running"])
        self.assertFalse(payload["simulation_fallback"])
        self.assertEqual("unknown", payload["control_state"])

    def test_load_daemon_status_falls_back_to_file_when_http_payload_malformed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = f"{tmpdir}\\trading_status.json"
            with open(status_path, "w", encoding="utf-8") as handle:
                json.dump({"is_running": True, "control_state": "blocked"}, handle)

            skill = TraderGatewaySkill(
                redis_client=None,
                http_client=_FakeHTTPClient(response=_FakeHTTPResponse(["broken"])),
            )
            skill.api_base = "http://trader"
            skill.status_path = status_path

            payload = asyncio.run(skill._load_daemon_status())

        self.assertTrue(payload["is_running"])
        self.assertEqual("blocked", payload["control_state"])

    def test_load_daemon_status_falls_back_to_file_when_http_times_out(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = f"{tmpdir}\\trading_status.json"
            with open(status_path, "w", encoding="utf-8") as handle:
                json.dump({"is_running": True, "control_state": "active"}, handle)

            skill = TraderGatewaySkill(
                redis_client=None,
                http_client=_FakeHTTPClient(exc=RuntimeError("timeout")),
            )
            skill.api_base = "http://trader"
            skill.status_path = status_path

            payload = asyncio.run(skill._load_daemon_status())

        self.assertTrue(payload["is_running"])
        self.assertEqual("active", payload["control_state"])


if __name__ == "__main__":
    unittest.main()

