from __future__ import annotations

from core.base import ElaraTool, NLPUnderstanding, Skill


class DeviceControlSkill(Skill):
    def __init__(self, state_manager=None, mqtt_service=None, device_config=None):
        self.state_manager = state_manager
        self.mqtt_service = mqtt_service
        self.device_config = device_config or {}

    def get_intents(self) -> list[str]:
        return ["turn_on_device", "turn_off_device"]

    def get_tools(self) -> list[ElaraTool]:
        return [
            ElaraTool(
                name="turn_on_device",
                description="Nyalakan device terkonfigurasi.",
                parameters={
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string"},
                        "room": {"type": "string"},
                    },
                    "required": ["device_id"],
                },
            ),
            ElaraTool(
                name="turn_off_device",
                description="Matikan device terkonfigurasi.",
                parameters={
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string"},
                        "room": {"type": "string"},
                    },
                    "required": ["device_id"],
                },
            ),
        ]

    def _device_topic(self, device_id: str, room: str) -> str:
        topics = dict((self.device_config or {}).get("topics") or {})
        base_topic = str(topics.get(device_id) or "").strip()
        room = str(room or "").strip()
        if base_topic and room:
            return f"{base_topic}/{room}"
        return base_topic

    async def execute_tool(self, name: str, args: dict, user_id: str = "default"):
        args = args or {}
        device_id = str(args.get("device_id") or args.get("device") or "").strip()
        room = str(args.get("room") or "").strip()
        intent = "turn_on_device" if str(name or "").strip().lower() == "turn_on_device" else "turn_off_device"
        nlu = NLPUnderstanding(
            intent=intent,
            confidence=1.0,
            raw_text=f"{intent} {device_id} {room}".strip(),
            source="tool_call",
        )
        return await self.handle(nlu, user_id=user_id)

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        text = str(getattr(nlu, "raw_text", "") or "")
        parts = [part for part in text.split() if part]
        device_id = parts[1] if len(parts) >= 2 else ""
        room = parts[2] if len(parts) >= 3 else ""
        topic = self._device_topic(device_id, room)
        is_on = str(getattr(nlu, "intent", "") or "") == "turn_on_device"
        action = "on" if is_on else "off"
        payload = {"power": bool(is_on), "action": action}
        if self.mqtt_service and topic and hasattr(self.mqtt_service, "publish"):
            self.mqtt_service.publish(topic, payload)
        elif self.mqtt_service and hasattr(self.mqtt_service, "control_device"):
            self.mqtt_service.control_device(device_id, room, action)
        if self.state_manager and hasattr(self.state_manager, "set_state"):
            self.state_manager.set_state(
                f"device:{device_id}:{room}:power",
                bool(is_on),
                user_id=user_id,
                ttl=300,
            )
        verb = "menyalakan" if is_on else "mematikan"
        location = f" di {room}" if room else ""
        return f"Berhasil {verb} {device_id}{location}."

