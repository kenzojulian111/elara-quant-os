import re
import logging
from typing import Optional, List

from core.base import Skill, NLPUnderstanding
from core.life import LifeManager

log = logging.getLogger("elara.skill.life")


class LifeSkill(Skill):
    def __init__(self, life_manager: LifeManager):
        self.life = life_manager

    def get_intents(self) -> List[str]:
        return ["update_status", "check_permission"]

    def _extract_float(self, text: str, pattern: str) -> Optional[float]:
        m = re.search(pattern, text)
        if not m:
            return None
        raw = m.group(1).replace(",", ".")
        try:
            return float(raw)
        except Exception:
            return None

    def _extract_int(self, text: str, pattern: str) -> Optional[int]:
        m = re.search(pattern, text)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default") -> Optional[str]:
        text = (nlu.raw_text or "").lower()

        if nlu.intent == "update_status":
            sleep = self._extract_float(text, r"(?:tidur|sleep)\s*(\d+(?:[.,]\d+)?)")
            if sleep is None:
                sleep = self._extract_float(text, r"(\d+(?:[.,]\d+)?)\s*jam")
            energy = self._extract_int(text, r"(?:energi|energy)\s*(\d+)")
            focus = self._extract_int(text, r"(?:fokus|focus)\s*(\d+)")
            stress = self._extract_int(text, r"stress\s*(\d+)")

            ctx = self.life.update(
                sleep_hours=sleep,
                energy=energy,
                focus_minutes=focus,
                stress=stress,
                user_id=user_id
            )

            mode = self.life.get_mode(user_id)
            return (
                "Data diterima. Mode "
                f"{mode} diaktifkan. "
                f"(Tidur: {ctx.sleep_hours} jam, Energi: {ctx.energy}, Fokus: {ctx.focus_minutes} menit, Stress: {ctx.stress})"
            )

        if nlu.intent == "check_permission":
            if any(k in text for k in ["trade", "trading", "binance", "btc", "crypto"]):
                decision = self.life.evaluate_trading(user_id)
                if decision.allowed:
                    return f"Boleh trading. {decision.reason}"
                return f"Belum boleh trading. {decision.reason}"

            decision = self.life.evaluate_entertainment(user_id)
            if decision.allowed:
                return f"Boleh hiburan. {decision.reason}"
            return f"Belum boleh hiburan. {decision.reason}"

        return None

