import logging
import threading
import time
import json
import os
import subprocess
try:
    import requests
except Exception:
    requests = None
from datetime import datetime, timezone
from config.settings import Config
from utils.health import DependencyCircuitBreaker
from utils.redis_helper import get_redis, redis_get_json
from utils.service_contracts import TraderStatusContract

log = logging.getLogger("elara.orchestrator")


class OrchestratorService:
    """
    Watchdog sederhana untuk:
    - Monitor status file trading (runtime/trading_status.json)
    - Monitor health snapshot (runtime/mqtt broadcast)
    - Auto-restart trading daemon atau kirim command 'flatten/stop' lewat IPC file
    """
    def __init__(
        self,
        trading_status_path=None,
        trading_command_path=None,
        trading_start_cmd=None,
        stop_daemon_on_exit=None,
        time_fn=None,
    ):
        self.trading_status_path = trading_status_path or Config.TRADING_STATUS_PATH
        self.trading_command_path = trading_command_path or Config.TRADING_COMMAND_PATH
        self.trading_start_cmd = trading_start_cmd or [os.sys.executable, "-m", "skills.advanced.trading.daemon_runner"]
        self.running = False
        self.thread = None
        self.restart_cooldown = 60
        self.last_restart = 0
        self.dd_limit_pct = float(getattr(Config, "SAFETY_MAX_DD_PCT", 15))
        self.block_timeout = int(getattr(Config, "SAFETY_BLOCK_TIMEOUT_SEC", 1200))
        self.latency_limit = float(getattr(Config, "SAFETY_LATENCY_SEC", 5))
        self.loop_interval_sec = max(1.0, float(getattr(Config, "ORCH_LOOP_INTERVAL_SEC", 15.0)))
        self.last_block_ts = None
        self._tg_enabled = getattr(Config, "SAFETY_TG_ALERT", False) and getattr(Config, "BOT_TOKEN", None) and getattr(Config, "TG_ALLOWED_USER", 0)
        self.redis = get_redis()
        self.redis_prefix = getattr(Config, "REDIS_PREFIX", "elara:")
        self._daemon_proc = None
        self._proc_lock = threading.Lock()
        self._time_fn = time_fn or time.time
        circuit_fail_threshold = getattr(Config, "ORCH_STATUS_CIRCUIT_FAIL_THRESHOLD", 2) or 2
        circuit_cooldown_sec = getattr(Config, "ORCH_STATUS_CIRCUIT_COOLDOWN_SEC", 120.0) or 120.0
        self.status_circuit = DependencyCircuitBreaker(
            fail_threshold=max(1, int(circuit_fail_threshold)),
            cooldown_sec=max(10.0, float(circuit_cooldown_sec)),
            time_fn=self._time_fn,
        )
        if stop_daemon_on_exit is None:
            stop_daemon_on_exit = bool(getattr(Config, "ORCH_STOP_DAEMON_ON_EXIT", False))
        self.stop_daemon_on_exit = bool(stop_daemon_on_exit)

    def _refresh_redis(self):
        if self.redis:
            return self.redis
        self.redis = get_redis()
        return self.redis

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="Orchestrator")
        self.thread.start()
        log.info("Orchestrator started.")

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        with self._proc_lock:
            if self._daemon_proc and self._daemon_proc.poll() is None:
                if self.stop_daemon_on_exit:
                    try:
                        self._daemon_proc.terminate()
                    except Exception as e:
                        log.debug(f"Orchestrator daemon terminate warning: {e}")
                else:
                    log.info("Orchestrator stop: trading daemon dibiarkan tetap berjalan (ORCH_STOP_DAEMON_ON_EXIT=0).")
            self._daemon_proc = None

    def _loop(self):
        while self.running:
            cycle_start = self._time_fn()
            try:
                status = self._observe_trading_status(self._read_trading_status())
                self._safety_checks(status)
                now = self._time_fn()
                if not status or not status.get("is_running"):
                    setup_cooldown = bool(status and self._is_setup_cooldown_active(status))
                    if setup_cooldown:
                        remain = self._setup_retry_remaining(status)
                        log.warning(
                            f"Trading setup failed recently. Delaying restart {remain}s "
                            f"(reason: {status.get('setup_error')})."
                        )
                    elif now - self.last_restart > self.restart_cooldown:
                        log.warning("Trading daemon offline. Attempting restart.")
                        self._start_trading_daemon()
                        self.last_restart = now
                        self._notify(f"Orchestrator: trading daemon offline, attempting restart.")
                elif status.get("life_blocked"):
                    # optional: flatten positions if blocked too long
                    log.debug("Trading marked life_blocked; waiting for timeout safety policy.")
            except Exception as e:
                log.warning(f"Orchestrator loop error: {e}")
            elapsed = self._time_fn() - cycle_start
            sleep_sec = max(0.1, self.loop_interval_sec - elapsed)
            time.sleep(sleep_sec)

    def _status_is_stale(self, data: dict) -> bool:
        max_age_sec = int(getattr(Config, "TRADING_STATUS_MAX_AGE_SEC", 120))
        if max_age_sec <= 0:
            return False
        ts = data.get("ts")
        if not ts:
            return False
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_sec = self._time_fn() - dt.timestamp()
            return age_sec > max_age_sec
        except Exception:
            return False

    def _is_setup_cooldown_active(self, status: dict) -> bool:
        contract = TraderStatusContract.from_payload(status)
        if not status or not contract.setup_error:
            return False
        retry_sec = int(getattr(Config, "TRADING_SETUP_RETRY_SEC", 300))
        if retry_sec <= 0:
            return False
        failed_at = status.get("setup_failed_at")
        if failed_at is None:
            return False
        try:
            age = self._time_fn() - float(failed_at)
            return age < retry_sec
        except Exception:
            return False

    def _setup_retry_remaining(self, status: dict) -> int:
        retry_sec = int(getattr(Config, "TRADING_SETUP_RETRY_SEC", 300))
        failed_at = status.get("setup_failed_at")
        try:
            return max(0, int(retry_sec - (self._time_fn() - float(failed_at))))
        except Exception:
            return retry_sec

    def _safety_checks(self, status):
        if not status:
            return
        contract = TraderStatusContract.from_payload(status)
        if contract.setup_error:
            return
        # drawdown check
        equity = status.get("equity")
        dd_pct = status.get("global_drawdown_pct")
        limit = float(status.get("max_drawdown_limit_pct", self.dd_limit_pct) or self.dd_limit_pct)
        if dd_pct is not None:
            try:
                dd_pct_f = float(dd_pct)
                if dd_pct_f >= limit:
                    log.error(f"Drawdown {dd_pct_f:.1f}% >= limit {limit}%. Sending FLATTEN+BLOCK.")
                    self.send_command("flatten")
                    self.send_command("block")
                    self._notify(f"[SAFETY] Drawdown {dd_pct_f:.1f}% >= {limit}%. Flatten+Block executed.")
            except Exception as e:
                log.error(f"Failed evaluating drawdown safety check: {e}")
        elif equity is not None:
            try:
                init_cap = float(getattr(Config, "INITIAL_CAPITAL", 1000))
                eq = float(equity)
                if eq <= 0 or init_cap <= 0:
                    return
                dd_pct_f = (init_cap - eq) / init_cap * 100
                if dd_pct_f >= limit:
                    log.error(f"Drawdown {dd_pct_f:.1f}% >= limit {limit}%. Sending FLATTEN+BLOCK.")
                    self.send_command("flatten")
                    self.send_command("block")
                    self._notify(f"[SAFETY] Drawdown {dd_pct_f:.1f}% >= {limit}%. Flatten+Block executed.")
            except Exception as e:
                log.error(f"Failed evaluating drawdown safety check: {e}")

        # life blocked timeout
        if status.get("life_blocked"):
            now = self._time_fn()
            if not self.last_block_ts:
                self.last_block_ts = now
            elif now - self.last_block_ts > self.block_timeout:
                log.warning("Life block timeout exceeded, forcing FLATTEN.")
                self.send_command("flatten")
                self._notify("Life block timeout exceeded; flatten issued.")
        else:
            self.last_block_ts = None

    def _read_trading_status(self):
        # prefer redis
        redis_client = self._refresh_redis()
        if redis_client:
            data = redis_get_json(redis_client, self.redis_prefix + "trading_status")
            if data:
                data = TraderStatusContract.from_payload(data).raw
                if self._status_is_stale(data):
                    data = dict(data)
                    data["is_running"] = False
                    data["stale"] = True
                return data
        if self.trading_status_path and os.path.exists(self.trading_status_path):
            try:
                with open(self.trading_status_path, "r") as f:
                    data = json.load(f)
                data = TraderStatusContract.from_payload(data).raw
                if isinstance(data, dict) and self._status_is_stale(data):
                    data = dict(data)
                    data["is_running"] = False
                    data["stale"] = True
                return data
            except Exception as e:
                log.warning(f"Failed reading trading status file '{self.trading_status_path}': {e}")
                return None
        return None

    def _dependency_states_from_status(self, status):
        if not status:
            return {"trader_status": "down"}
        contract = TraderStatusContract.from_payload(status)
        states = {"trader_status": "up"}
        if contract.stale:
            states["trader_status"] = "stale"
        elif not contract.is_running:
            states["trader_status"] = "down"
        if contract.setup_error:
            states["trader_setup"] = "down"
        return states

    def _observe_trading_status(self, status):
        contract = TraderStatusContract.from_payload(status)
        observed = dict(contract.raw)
        if not observed:
            observed["is_running"] = False
        dependency_observation = self.status_circuit.observe(self._dependency_states_from_status(observed if status else None))
        observed["dependency_states"] = dict(dependency_observation.states)
        if dependency_observation.reasons:
            observed["dependency_circuit_reasons"] = tuple(dependency_observation.reasons)
        if any(
            str(state or "").strip().lower() in {"down", "failed", "offline", "unavailable"}
            for state in dependency_observation.states.values()
        ):
            observed["is_running"] = False
        return observed

    def _start_trading_daemon(self):
        with self._proc_lock:
            if self._daemon_proc and self._daemon_proc.poll() is None:
                return
        try:
            env = os.environ.copy()
            root_path = getattr(Config, "root_path", os.getcwd())
            py_path = env.get("PYTHONPATH", "")
            if root_path and root_path not in py_path.split(os.pathsep):
                env["PYTHONPATH"] = root_path + (os.pathsep + py_path if py_path else "")

            proc = subprocess.Popen(
                self.trading_start_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=root_path,
                env=env,
            )
            with self._proc_lock:
                self._daemon_proc = proc
        except Exception as e:
            log.error(f"Gagal start trading daemon: {e}")

    def send_command(self, cmd: str):
        if not self.trading_command_path:
            return False
        try:
            os.makedirs(os.path.dirname(self.trading_command_path), exist_ok=True)
            with open(self.trading_command_path, "w") as f:
                json.dump([{"cmd": cmd, "priority": 1}], f)
            return True
        except Exception as e:
            log.error(f"Gagal kirim command: {e}")
            return False

    def _notify(self, msg: str):
        if not self._tg_enabled or requests is None:
            return
        try:
            token = Config.BOT_TOKEN
            chat_id = Config.TG_ALLOWED_USER
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=3)
        except Exception as e:
            log.warning(f"Orchestrator notification failed: {e}")

