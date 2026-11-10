import logging
from typing import Optional

from core.base import Skill, NLPUnderstanding
from config.settings import Config

log = logging.getLogger("elara.skill.selfops")


class SelfOpsSkill(Skill):
    def __init__(self, self_heal_service):
        self.self_heal_service = self_heal_service

    def get_intents(self):
        return ["self_heal", "self_deploy", "self_deploy_status"]

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default") -> Optional[str]:
        if not self.self_heal_service:
            return "Self-ops service belum aktif."

        if nlu.intent == "self_heal":
            report = self.self_heal_service.heal_now()
            actions = report.get("actions", [])
            warns = report.get("warnings", [])
            health_score = float(report.get("health_score", 0.0) or 0.0)
            health_severity = str(report.get("health_severity") or "-")
            return (
                f"Self-heal selesai. health={health_score:.1f} ({health_severity}), "
                f"actions={len(actions)}, warnings={len(warns)}. "
                f"Detail: {actions[:6]}"
            )

        if nlu.intent == "self_deploy":
            if not getattr(Config, "SELF_DEPLOY_ENABLED", False):
                return "Self-deploy nonaktif. Set SELF_DEPLOY_ENABLED=1 untuk mengaktifkan."
            txt = (nlu.raw_text or "").lower()
            pull = bool(getattr(Config, "SELF_DEPLOY_AUTO_PULL", True))
            install = bool(getattr(Config, "SELF_DEPLOY_AUTO_INSTALL", False))
            restart = bool(getattr(Config, "SELF_DEPLOY_AUTO_RESTART", False))

            if any(k in txt for k in ["no pull", "tanpa pull", "skip pull"]):
                pull = False
            elif "pull" in txt:
                pull = True

            if any(k in txt for k in ["install", "dependencies", "dependency", "deps", "dependensi"]):
                install = True

            if "restart" in txt:
                restart = True
            result = self.self_heal_service.deploy_now(pull=pull, install=install, restart=restart)
            if result.get("started"):
                return "Self-deploy dimulai di background."
            return f"Self-deploy tidak dijalankan: {result.get('message')}"

        if nlu.intent == "self_deploy_status":
            status = self.self_heal_service.status()
            last = status.get("last_deploy_report") or {}
            if status.get("deploy_running"):
                return "Self-deploy sedang berjalan."
            if not last:
                return "Belum ada riwayat self-deploy."
            return f"Self-deploy terakhir: ok={last.get('ok')} msg={last.get('message')}"

        return None

