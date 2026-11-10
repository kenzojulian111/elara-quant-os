import logging
import requests
from core.base import Skill, NLPUnderstanding, ElaraTool
from config.settings import Config

log = logging.getLogger("elara.skill.adguard")


class HomeAdguardSkill(Skill):
    def __init__(self):
        self.base = Config.ADGUARD_HOST.rstrip("/") if getattr(Config, "ADGUARD_HOST", "") else ""
        self.auth = (getattr(Config, "ADGUARD_USER", ""), getattr(Config, "ADGUARD_PASS", ""))
        self.timeout = int(getattr(Config, "ADGUARD_TIMEOUT", 5))

    def get_intents(self):
        return ["adguard_enable", "adguard_disable", "adguard_status", "adguard_block_domain"]

    def get_tools(self):
        return [
            ElaraTool(
                name="adguard_enable",
                description="Enable AdGuard protection.",
                parameters={"type": "object", "properties": {}},
            ),
            ElaraTool(
                name="adguard_disable",
                description="Disable AdGuard protection.",
                parameters={"type": "object", "properties": {}},
            ),
            ElaraTool(
                name="adguard_status",
                description="Get current AdGuard protection status.",
                parameters={"type": "object", "properties": {}},
            ),
            ElaraTool(
                name="adguard_block_domain",
                description="Block a domain in AdGuard.",
                parameters={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Domain name to block"},
                    },
                    "required": ["domain"],
                },
            ),
        ]

    async def execute_tool(self, name: str, args: dict, user_id: str = "default") -> str:
        payload = args if isinstance(args, dict) else {}
        raw_text = f"tool:{name}"
        if name == "adguard_block_domain":
            domain = str(payload.get("domain") or "").strip()
            raw_text = f"blokir domain {domain}".strip()
        nlu = NLPUnderstanding(
            intent=name,
            confidence=1.0,
            entities=[],
            raw_text=raw_text,
            source="tool_call",
        )
        result = await self.handle(nlu, user_id=user_id)
        return result or f"Tool {name} selesai."

    def _req(self, method, path, **kwargs):
        if not self.base:
            raise RuntimeError("ADGUARD_HOST tidak diset")
        url = f"{self.base}{path}"
        auth = None
        if self.auth[0] or self.auth[1]:
            auth = self.auth
        return requests.request(method, url, auth=auth, timeout=self.timeout, **kwargs)

    async def handle(self, nlu: NLPUnderstanding, user_id: str = "default"):
        intent = nlu.intent
        try:
            if intent == "adguard_enable":
                self._req("POST", "/control/protection/enable")
                return "AdGuard diaktifkan."
            if intent == "adguard_disable":
                self._req("POST", "/control/protection/disable")
                return "AdGuard dimatikan."
            if intent == "adguard_status":
                r = self._req("GET", "/control/status")
                data = r.json()
                prot = data.get("protection_enabled")
                filt = data.get("filtering_status")
                return f"AdGuard status: {'ON' if prot else 'OFF'}, filtering: {filt}"
            if intent == "adguard_block_domain":
                # expect domain in raw_text
                parts = (nlu.raw_text or "").split()
                domain = None
                for p in parts:
                    if "." in p:
                        domain = p
                        break
                if not domain:
                    return "Domain apa yang mau diblok?"
                self._req("POST", "/control/parental/block", json={"domain": domain})
                return f"Domain {domain} ditambahkan ke blokir."
        except Exception as e:
            log.error(f"AdGuard error: {e}")
            return f"AdGuard error: {e}"
        return "Perintah AdGuard tidak dikenali."

