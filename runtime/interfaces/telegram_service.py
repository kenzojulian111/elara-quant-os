import asyncio
import json
import logging
import os
import re
import signal
import time

try:
    import telebot

    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False
    telebot = None

from config.settings import Config
from core.events import build_trade_command_message, trade_command_queue_key
from utils.common import async_runner
from utils.redis_helper import get_redis

log = logging.getLogger("elara.telegram")


class TelegramService:
    def __init__(self, elara_skill):
        self.elara = elara_skill
        self.bot = None
        self._polling_task = None
        self._supervisor_task = None
        self._running = False
        self._disabled_until = 0.0
        self._last_start_error = ""
        self._concurrency = max(1, int(getattr(Config, "TG_HANDLER_MAX_CONCURRENCY", 4)))
        self._skip_pending = bool(getattr(Config, "TG_SKIP_PENDING", True))
        self._reject_log_cooldown_sec = max(5, int(getattr(Config, "TG_REJECT_LOG_COOLDOWN_SEC", 30)))
        self._last_reject_log_ts = 0.0
        self._busy_sem = None
        self._polling_conflict_cooldown_sec = max(30, int(getattr(Config, "TG_POLLING_CONFLICT_COOLDOWN_SEC", 300)))
        self._polling_restart_backoff_sec = max(1, int(getattr(Config, "TG_POLLING_RESTART_BACKOFF_SEC", 3)))
        self._skill_shortcuts_enabled = bool(getattr(Config, "TG_SKILL_SHORTCUTS_ENABLED", True))
        self._skill_shortcut_aliases = self._load_skill_shortcut_aliases()
        self._last_chat_id = 0
        self._default_empty_reply = str(
            getattr(
                Config,
                "TG_EMPTY_RESPONSE_FALLBACK",
                "Perintah diterima, tapi belum ada output dari skill. Coba ulang atau cek log sistem.",
            )
            or "Perintah diterima, tapi belum ada output dari skill."
        ).strip()

    @staticmethod
    def _normalize_skill_name(raw: str) -> str:
        value = str(raw or "").strip().lower().replace("_", "-")
        if not value:
            return ""
        if not re.match(r"^[a-z0-9][a-z0-9\-]{1,63}$", value):
            return ""
        return value

    @staticmethod
    def _strip_command_bot_mention(text: str) -> str:
        raw = str(text or "").strip()
        if not raw.startswith("/"):
            return raw
        parts = raw.split(maxsplit=1)
        cmd = parts[0]
        if "@" not in cmd:
            return raw
        base = cmd.split("@", 1)[0]
        rest = parts[1] if len(parts) > 1 else ""
        return f"{base} {rest}".strip()

    def _load_skill_shortcut_aliases(self):
        default_aliases = {
            "ops": "elara-trading-ops",
            "debug": "systematic-debugging",
            "verify": "verification-before-completion",
            "test": "webapp-testing",
            "api": "api-testing-observability-api-mock",
            "sec": "api-security-best-practices",
        }
        raw = str(getattr(Config, "TG_SKILL_SHORTCUT_ALIASES", "") or "").strip()
        if not raw:
            return default_aliases

        parsed = {}
        for item in raw.split(","):
            row = str(item or "").strip()
            if not row or "=" not in row:
                continue
            alias_raw, skill_raw = row.split("=", 1)
            alias = str(alias_raw or "").strip().lower()
            skill = self._normalize_skill_name(skill_raw)
            if not alias or not skill:
                continue
            parsed[alias] = skill
        if not parsed:
            return default_aliases
        return parsed

    def _shortcut_help_text(self) -> str:
        aliases = sorted(self._skill_shortcut_aliases.items())
        alias_lines = [f"- /{alias} <instruksi> -> pakai {skill}" for alias, skill in aliases]
        base_lines = [
            "Shortcut Telegram Elara:",
            "- /skills : daftar my-skill",
            "- /skills <kata-kunci> : cari my-skill",
            "- /skills policy : status policy my-skill",
            "- /findskill <kata-kunci> : cari my-skill",
            "- /skill <nama> : detail skill",
            "- /sk <nama> <instruksi> : jalankan skill cepat",
        ]
        base_lines.extend(alias_lines)
        return "\n".join(base_lines)

    def _rewrite_skill_shortcut(self, text: str):
        raw = self._strip_command_bot_mention(text)
        low = raw.lower()
        if not self._skill_shortcuts_enabled:
            return raw, None

        if low in {"/help", "/shortcuts", "/skillhelp", "/menu"}:
            return "", self._shortcut_help_text()

        if low in {"/skills", "/myskills", "/listskills"}:
            return "/skills", None

        if low in {"/findskill", "/searchskill", "/fs"}:
            return "", "Format: /findskill <kata-kunci>"

        fs_match = re.match(r"^/fs\s+(.+)$", raw, flags=re.IGNORECASE)
        if fs_match:
            keyword = str(fs_match.group(1) or "").strip()
            if keyword:
                return f"/findskill {keyword}", None

        if low in {"/sk", "/skill"}:
            return "", "Format: /sk <nama-skill> <instruksi>. Cek /skills untuk daftar."

        detail_match = re.match(r"^/(?:skill)\s+([a-z0-9][a-z0-9\-_]{1,63})\s*$", low)
        if detail_match:
            skill_name = self._normalize_skill_name(detail_match.group(1))
            if skill_name:
                return f"/skill {skill_name}", None
            return "", "Format skill tidak valid. Contoh: /skill elara-trading-ops"

        run_match = re.match(
            r"^/(?:sk|skill)\s+([a-z0-9][a-z0-9\-_]{1,63})(?:\s+(.+))?$",
            raw,
            flags=re.IGNORECASE,
        )
        if run_match:
            skill_name = self._normalize_skill_name(run_match.group(1))
            prompt = str(run_match.group(2) or "").strip()
            if not skill_name:
                return "", "Nama skill tidak valid."
            if not prompt:
                return f"/skill {skill_name}", None
            return f"pakai {skill_name}, {prompt}", None

        alias_match = re.match(r"^/([a-z0-9][a-z0-9\-]{1,32})(?:\s+(.+))?$", raw, flags=re.IGNORECASE)
        if alias_match:
            alias = str(alias_match.group(1) or "").strip().lower()
            prompt = str(alias_match.group(2) or "").strip()
            target_skill = self._skill_shortcut_aliases.get(alias)
            if target_skill:
                if not prompt:
                    return "", f"Format: /{alias} <instruksi> (skill: {target_skill})"
                return f"pakai {target_skill}, {prompt}", None

        return raw, None

    @staticmethod
    def _is_polling_conflict(msg: str) -> bool:
        text = str(msg or "").lower()
        return "error code: 409" in text or "conflict" in text

    def _queue_conflict_failsafe_commands(self, reason: str) -> bool:
        redis_client = get_redis()
        if not redis_client:
            log.critical("Telegram 409 fail-safe could not enqueue commands: Redis unavailable.")
            return False

        redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
        queue_key = str(
            getattr(Config, "TRADING_COMMAND_KEY", trade_command_queue_key(redis_prefix))
            or trade_command_queue_key(redis_prefix)
        ).strip()
        try:
            for cmd in ("flatten", "stop"):
                payload = build_trade_command_message(
                    cmd=cmd,
                    source="telegram_409_guard",
                    reason=reason,
                    user_id="system",
                    priority=0,
                )
                redis_client.lpush(queue_key, json.dumps(payload))
            return True
        except Exception as e:
            log.critical(f"Telegram 409 fail-safe enqueue failed: {e}")
            return False

    async def _trigger_polling_conflict_failsafe(self):
        reason = "telegram polling conflict detected (error 409)"
        queued = await asyncio.to_thread(self._queue_conflict_failsafe_commands, reason)
        if queued:
            log.critical("Telegram 409 fail-safe queued flatten+stop commands to trader.")
        self.stop()
        log.critical("Telegram token conflict detected. Core process will be terminated.")
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            log.critical(f"Failed to terminate process after Telegram 409 conflict: {e}")

    def _log_rejected_chat(self, chat_id: int):
        now = time.time()
        if (now - self._last_reject_log_ts) < self._reject_log_cooldown_sec:
            return
        self._last_reject_log_ts = now
        log.warning(
            "Telegram message ignored: chat_id=%s not allowed (TG_ALLOWED_USER=%s).",
            int(chat_id or 0),
            int(getattr(Config, "TG_ALLOWED_USER", 0) or 0),
        )

    def start(self):
        if not TG_AVAILABLE or not Config.BOT_TOKEN:
            log.warning("Telegram Bot Disabled (No Token/Lib).")
            return
        if Config.TG_ALLOWED_USER == 0:
            log.warning("TG_ALLOWED_USER belum diset. Telegram bot berjalan dalam mode allow-all chat.")
        if ":" not in Config.BOT_TOKEN:
            log.warning("Telegram Bot Disabled (token format invalid, missing colon).")
            return

        self._busy_sem = asyncio.Semaphore(self._concurrency)

        async def _process_message(message):
            if not self.bot:
                return
            if Config.TG_ALLOWED_USER and message.chat.id != Config.TG_ALLOWED_USER:
                self._log_rejected_chat(message.chat.id)
                return
            try:
                self._last_chat_id = int(message.chat.id or 0)
            except Exception:
                self._last_chat_id = 0
            text = message.text
            if not text:
                return
            normalized_text, local_reply = self._rewrite_skill_shortcut(text)
            if local_reply:
                await asyncio.to_thread(self.bot.send_message, message.chat.id, local_reply)
                return
            if not normalized_text:
                return
            async with self._busy_sem:
                try:
                    await asyncio.to_thread(self.bot.reply_to, message, "...")
                    response = await asyncio.to_thread(
                        async_runner.run_coroutine,
                        self.elara.handle_command(normalized_text, user_id=str(message.chat.id)),
                    )
                    response_text = str(response or "").strip() or self._default_empty_reply
                    await asyncio.to_thread(self.bot.send_message, message.chat.id, response_text)
                except Exception as e:
                    try:
                        await asyncio.to_thread(self.bot.reply_to, message, f"Error: {e}")
                    except Exception:
                        pass

        def _ensure_bot_handlers():
            if self.bot:
                return
            self.bot = telebot.TeleBot(Config.BOT_TOKEN)

            @self.bot.message_handler(commands=["start"])
            def handle_start(message):
                self.bot.reply_to(message, "Halo, Elara Modular siap.")

            @self.bot.message_handler(commands=["dashboard"])
            def handle_dashboard(message):
                if Config.TG_ALLOWED_USER and message.chat.id != Config.TG_ALLOWED_USER:
                    self._log_rejected_chat(message.chat.id)
                    return
                if not Config.TG_WEBAPP_URL or not Config.TG_WEBAPP_TOKEN:
                    self.bot.reply_to(message, "TG WebApp belum dikonfigurasi.")
                    return
                try:
                    from telebot import types

                    url = f"{Config.TG_WEBAPP_URL}?token={Config.TG_WEBAPP_TOKEN}"
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("Open Dashboard", web_app=types.WebAppInfo(url=url)))
                    self.bot.send_message(message.chat.id, "Elara Dashboard:", reply_markup=markup)
                except Exception as e:
                    self.bot.reply_to(message, f"Error: {e}")

            @self.bot.message_handler(func=lambda message: True)
            def handle_message(message):
                async_runner.run_coroutine_fire_and_forget(_process_message(message))

        async def _polling_runner():
            backoff = self._polling_restart_backoff_sec
            while self._running:
                now = time.time()
                if now < self._disabled_until:
                    await asyncio.sleep(1.0)
                    continue
                try:
                    _ensure_bot_handlers()
                    await asyncio.to_thread(
                        self.bot.infinity_polling,
                        skip_pending=self._skip_pending,
                        timeout=20,
                        long_polling_timeout=20,
                    )
                    backoff = self._polling_restart_backoff_sec
                except Exception as e:
                    self._last_start_error = str(e)
                    msg = self._last_start_error.lower()
                    if self._is_polling_conflict(msg):
                        await self._trigger_polling_conflict_failsafe()
                        return
                    else:
                        log.warning(f"Telegram polling crashed: {e}")
                        await asyncio.sleep(backoff)
                        backoff = min(60, backoff * 2)
                await asyncio.sleep(0.2)

        async def _supervisor():
            while self._running:
                if not self._polling_task or self._polling_task.done():
                    self._polling_task = async_runner.run_coroutine_fire_and_forget(_polling_runner())
                await asyncio.sleep(2.0)

        self._running = True
        self._supervisor_task = async_runner.run_coroutine_fire_and_forget(_supervisor())
        log.info("Telegram Bot Started in Background.")

    def stop(self):
        self._running = False
        if self.bot:
            try:
                self.bot.stop_polling()
            except Exception as e:
                log.warning(f"Telegram stop polling failed: {e}")
        current_task = None
        try:
            current_task = asyncio.current_task()
        except Exception:
            current_task = None
        if self._polling_task and not self._polling_task.done() and self._polling_task is not current_task:
            self._polling_task.cancel()
        if self._supervisor_task and not self._supervisor_task.done() and self._supervisor_task is not current_task:
            self._supervisor_task.cancel()
        self.bot = None

    def notify(self, message):
        if not self.bot:
            return
        target_chat_id = int(getattr(Config, "TG_ALLOWED_USER", 0) or 0)
        if target_chat_id == 0:
            target_chat_id = int(self._last_chat_id or 0)
        if target_chat_id == 0:
            return
        try:
            self.bot.send_message(target_chat_id, message)
        except Exception as e:
            log.error(f"TG Notify Error: {e}")

    def is_healthy(self):
        if not TG_AVAILABLE or not Config.BOT_TOKEN:
            return True
        if time.time() < self._disabled_until:
            return True
        if not self._running:
            return True
        if self._supervisor_task is not None and self._supervisor_task.done():
            return False
        return self._polling_task is not None and not self._polling_task.done()

