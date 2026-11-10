import asyncio
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from skills.DeviceControlSkill import DeviceControlSkill
from skills.TraderGatewaySkill import TraderGatewaySkill
from skills.WeatherSkill import WeatherSkill


class _FakeStateManager:
    def __init__(self):
        self._data = {}

    def get_state(self, key, user_id="default"):
        return self._data.get((user_id, key))

    def set_state(self, key, value, user_id="default", ttl=None):
        _ = ttl
        self._data[(user_id, key)] = value


class _FakeMQTT:
    def __init__(self):
        self.published = []
        self.controls = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return True

    def control_device(self, device, room, action, value=None):
        self.controls.append((device, room, action, value))
        return True


class SkillToolMigrationTests(unittest.TestCase):
    def test_device_control_execute_tool_supports_device_id(self):
        state = _FakeStateManager()
        mqtt = _FakeMQTT()
        skill = DeviceControlSkill(
            state_manager=state,
            mqtt_service=mqtt,
            device_config={"topics": {"lampu": "rumah/lampu"}},
        )
        reply = asyncio.run(
            skill.execute_tool(
                "turn_on_device",
                {"device_id": "lampu", "room": "kamar"},
                user_id="u1",
            )
        )
        self.assertIn("menyalakan lampu", reply.lower())
        self.assertEqual(len(mqtt.published), 1)
        self.assertEqual(mqtt.published[0][0], "rumah/lampu/kamar")

    def test_weather_execute_tool_supports_cek_cuaca_alias(self):
        skill = WeatherSkill()
        with patch.object(Config, "WEATHER_API_KEY", ""):
            reply = asyncio.run(skill.execute_tool("cek_cuaca", {"kota": "Jakarta"}, user_id="u1"))
        self.assertIn("cuaca", reply.lower())
        self.assertIn("jakarta", reply.lower())

    def test_trader_gateway_tools_execute(self):
        skill = TraderGatewaySkill()
        skill._load_daemon_status = lambda: {
            "is_running": True,
            "equity": 1000.0,
            "cash": 800.0,
            "open_positions": 1,
            "daily_drawdown_pct": 0.2,
            "global_drawdown_pct": 1.0,
            "lite_mode": False,
            "ram_usage_pct": 30.0,
        }
        sent = {"cmd": None}

        def _send(cmd, source="core"):
            sent["cmd"] = cmd
            return True

        skill._send_daemon_command = _send

        status_reply = asyncio.run(skill.execute_tool("get_trading_status", {}, user_id="u2"))
        self.assertIn("hedge fund bot", status_reply.lower())
        flatten_reply = asyncio.run(skill.execute_tool("flatten_all_positions", {"reason": "risk"}, user_id="u2"))
        self.assertIn("flatten_all_positions", flatten_reply.lower())
        self.assertEqual(sent["cmd"], "flatten")


if __name__ == "__main__":
    unittest.main()

