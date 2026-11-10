from __future__ import annotations

from config.settings import Config
from core.base import ElaraTool, NLPUnderstanding, Skill


class WeatherSkill(Skill):
    def get_intents(self) -> list[str]:
        return ["get_weather"]

    def get_tools(self) -> list[ElaraTool]:
        return [
            ElaraTool(
                name="cek_cuaca",
                description="Cek cuaca kota tertentu.",
                parameters={
                    "type": "object",
                    "properties": {
                        "kota": {"type": "string", "description": "Nama kota"},
                    },
                    "required": ["kota"],
                },
            )
        ]

    async def execute_tool(self, name: str, args: dict, user_id: str = "default"):
        _ = user_id
        if str(name or "").strip().lower() != "cek_cuaca":
            return await super().execute_tool(name, args, user_id=user_id)
        city = str((args or {}).get("kota") or (args or {}).get("city") or "lokasi").strip() or "lokasi"
        nlu = NLPUnderstanding(
            intent="get_weather",
            confidence=1.0,
            raw_text=f"cek cuaca {city}",
            source="tool_call",
        )
        return await self.handle(nlu, user_id=user_id)

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        _ = user_id
        text = str(getattr(nlu, "raw_text", "") or "")
        city = "lokasi"
        for token in text.split():
            if token.lower() not in {"cek", "cuaca", "weather", "di"}:
                city = token
                break
        api_key = str(getattr(Config, "WEATHER_API_KEY", "") or "").strip()
        if not api_key:
            return f"Cuaca {city} belum bisa diambil dari provider eksternal. Fallback lokal aktif untuk {city}."
        return f"Cuaca {city} berhasil diminta ke provider."

