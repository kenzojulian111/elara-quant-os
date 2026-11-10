import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config.settings import Config

log = logging.getLogger("elara.selfops")


class SelfOpsManager:
    def __init__(self, root_path: Optional[str] = None, runtime_path: Optional[str] = None):
        self.root_path = root_path or getattr(Config, "root_path", os.getcwd())
        self.runtime_path = runtime_path or os.path.join(self.root_path, "runtime")
        self._heal_history_path = str(
            getattr(
                Config,
                "SELF_HEAL_HISTORY_PATH",
                os.path.join(self.runtime_path, "self_ops_heal_history.json"),
            )
            or os.path.join(self.runtime_path, "self_ops_heal_history.json")
        )
        self.last_heal_report: Dict = {}
        self.last_deploy_report: Dict = {}
        self.last_config_drift: Dict = {}
        self._heal_lock = threading.Lock()
        self._deploy_lock = threading.Lock()
        self._deploy_thread: Optional[threading.Thread] = None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _safe_json_load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ensure_parent_dir(self, path: str):
        parent = os.path.dirname(str(path or ""))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _safe_json_dump(self, path: str, data):
        self._ensure_parent_dir(path)
        tmp_path = f"{path}.{os.getpid()}.{int(time.time() * 1000)}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def _truncate(self, text: str, limit: int = 1200) -> str:
        if text is None:
            return ""
        text = str(text)
        if len(text) <= limit:
            return text
        return text[:limit] + "...<truncated>"

    def _path_age_sec(self, path: str) -> Optional[float]:
        if not path:
            return None
        try:
            if not os.path.exists(path):
                return None
            return max(0.0, time.time() - float(os.path.getmtime(path)))
        except Exception:
            return None

    def _dir_size_mb(self, path: str) -> float:
        total = 0
        if not path or not os.path.exists(path):
            return 0.0
        try:
            for root, _, files in os.walk(path):
                for name in files:
                    full = os.path.join(root, name)
                    try:
                        total += int(os.path.getsize(full))
                    except Exception:
                        continue
        except Exception:
            return 0.0
        return float(total / (1024 * 1024))

    def _collect_runtime_health(self, actions: List[str], warnings: List[str]) -> Dict:
        health = {
            "score": 100.0,
            "severity": "ok",
            "disk_free_pct": 0.0,
            "runtime_size_mb": 0.0,
            "state_age_sec": None,
            "command_age_sec": None,
            "status_age_sec": None,
            "git_branch": "",
            "git_ahead": 0,
            "git_behind": 0,
        }

        try:
            usage = shutil.disk_usage(self.runtime_path if os.path.exists(self.runtime_path) else self.root_path)
            total = max(1, int(usage.total))
            free = max(0, int(usage.free))
            free_pct = (float(free) / float(total)) * 100.0
            health["disk_free_pct"] = free_pct
            if free_pct < 10:
                health["score"] -= 25
                warnings.append(f"health_disk_critical:{free_pct:.1f}%")
            elif free_pct < 20:
                health["score"] -= 12
                warnings.append(f"health_disk_low:{free_pct:.1f}%")
        except Exception as e:
            warnings.append(f"health_disk_check_failed:{self._truncate(str(e), 120)}")
            health["score"] -= 8

        runtime_size_mb = self._dir_size_mb(self.runtime_path)
        health["runtime_size_mb"] = runtime_size_mb
        runtime_max_mb = max(128.0, float(getattr(Config, "SELF_HEAL_RUNTIME_MAX_MB", 4096.0)))
        if runtime_size_mb > runtime_max_mb:
            penalty = min(20.0, (runtime_size_mb - runtime_max_mb) / max(runtime_max_mb, 1.0) * 25.0)
            health["score"] -= penalty
            warnings.append(f"health_runtime_size_high:{runtime_size_mb:.1f}MB")

        state_path = str(getattr(Config, "STATE_DB_PATH", os.path.join(self.runtime_path, "state_memory.json")) or "")
        command_path = str(getattr(Config, "TRADING_COMMAND_PATH", os.path.join(self.runtime_path, "trading_command.json")) or "")
        status_path = str(getattr(Config, "TRADING_STATUS_PATH", os.path.join(self.runtime_path, "trading_status.json")) or "")
        state_age = self._path_age_sec(state_path)
        command_age = self._path_age_sec(command_path)
        status_age = self._path_age_sec(status_path)
        health["state_age_sec"] = state_age
        health["command_age_sec"] = command_age
        health["status_age_sec"] = status_age

        stale_state = max(60, int(getattr(Config, "SELF_HEAL_STATE_STALE_SEC", 21600)))
        stale_status = max(30, int(getattr(Config, "SELF_HEAL_STATUS_STALE_SEC", 900)))
        if state_age is not None and state_age > stale_state:
            health["score"] -= 6
            warnings.append(f"health_state_stale:{int(state_age)}s")
        if status_age is not None and status_age > stale_status:
            health["score"] -= 10
            warnings.append(f"health_status_stale:{int(status_age)}s")
        if command_age is not None and command_age > (stale_status * 2):
            health["score"] -= 4
            warnings.append(f"health_command_stale:{int(command_age)}s")

        try:
            branch_step = self._run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout_sec=20)
            branch = str(branch_step.get("stdout", "") or "").strip()
            if branch_step.get("returncode") == 0 and branch:
                health["git_branch"] = branch
                drift_step = self._run_command(
                    ["git", "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"],
                    timeout_sec=20,
                )
                if drift_step.get("returncode") == 0:
                    parts = str(drift_step.get("stdout", "") or "").strip().split()
                    if len(parts) >= 2:
                        behind = max(0, int(parts[0]))
                        ahead = max(0, int(parts[1]))
                        health["git_behind"] = behind
                        health["git_ahead"] = ahead
                        if behind > 0:
                            health["score"] -= min(14.0, float(behind) * 2.0)
                            warnings.append(f"health_git_behind:{behind}")
                        if ahead > 20:
                            health["score"] -= 4
                            warnings.append(f"health_git_ahead_large:{ahead}")
            else:
                actions.append("health_git_skipped")
        except Exception as e:
            warnings.append(f"health_git_check_failed:{self._truncate(str(e), 120)}")
            health["score"] -= 4

        health["score"] = max(0.0, min(100.0, float(health["score"])))
        score = health["score"]
        if score < 45:
            health["severity"] = "critical"
        elif score < 65:
            health["severity"] = "warning"
        else:
            health["severity"] = "ok"
        return health

    def _load_heal_history(self) -> List[Dict]:
        try:
            data = self._safe_json_load(self._heal_history_path)
            if isinstance(data, list):
                return [dict(item) for item in data if isinstance(item, dict)]
        except Exception:
            return []
        return []

    def _save_heal_history(self, rows: List[Dict]):
        self._safe_json_dump(self._heal_history_path, rows)

    def _append_heal_history(self, report: Dict) -> List[Dict]:
        rows = self._load_heal_history()
        rows.append(
            {
                "ts": str(report.get("ts") or self._now_iso()),
                "ok": bool(report.get("ok", False)),
                "health_score": float(report.get("health_score", 0.0) or 0.0),
                "warnings": int(len(report.get("warnings") or [])),
                "actions": int(len(report.get("actions") or [])),
                "duration_ms": int(report.get("duration_ms") or 0),
            }
        )
        keep = max(20, int(getattr(Config, "SELF_HEAL_HISTORY_MAX_ITEMS", 500)))
        if len(rows) > keep:
            rows = rows[-keep:]
        self._save_heal_history(rows)
        return rows

    def _detect_heal_anomaly(self, history_rows: List[Dict]) -> str:
        rows = list(history_rows or [])
        min_points = max(5, int(getattr(Config, "SELF_HEAL_ANOMALY_MIN_POINTS", 8)))
        if len(rows) < min_points:
            return ""
        tail = rows[-min_points:]
        latest = float(tail[-1].get("health_score", 0.0) or 0.0)
        baseline_vals = [float(item.get("health_score", 0.0) or 0.0) for item in tail[:-1]]
        if not baseline_vals:
            return ""
        baseline = sum(baseline_vals) / len(baseline_vals)
        drop_threshold = max(5.0, float(getattr(Config, "SELF_HEAL_ANOMALY_DROP_THRESHOLD", 15.0)))
        if (baseline - latest) >= drop_threshold:
            return (
                f"health_score_anomaly: latest={latest:.1f} baseline={baseline:.1f} "
                f"drop={baseline - latest:.1f}"
            )
        return ""

    def _rotate_log_if_needed(self, path: str, max_mb: int, actions: List[str]):
        if not os.path.exists(path):
            return
        if max_mb <= 0:
            return
        max_bytes = max_mb * 1024 * 1024
        try:
            size = os.path.getsize(path)
            if size <= max_bytes:
                return
            rotated = path + ".1"
            try:
                if os.path.exists(rotated):
                    os.remove(rotated)
            except Exception:
                pass
            os.replace(path, rotated)
            actions.append(f"log_rotated:{os.path.basename(path)}")
        except Exception as e:
            actions.append(f"log_rotate_failed:{os.path.basename(path)}:{e}")

    def _cleanup_tmp_files(self, base_dir: str, max_age_sec: int, actions: List[str]):
        if not os.path.exists(base_dir):
            return
        now = time.time()
        for name in os.listdir(base_dir):
            if not (name.endswith(".tmp") or name.startswith(".state_")):
                continue
            path = os.path.join(base_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                age = now - os.path.getmtime(path)
                if age > max_age_sec:
                    os.remove(path)
                    actions.append(f"tmp_removed:{name}")
            except Exception as e:
                actions.append(f"tmp_cleanup_failed:{name}:{e}")

    def _repair_state_file(self, state_path: str, actions: List[str]):
        self._ensure_parent_dir(state_path)
        if not os.path.exists(state_path):
            self._safe_json_dump(state_path, {})
            actions.append("state_created")
            return
        try:
            data = self._safe_json_load(state_path)
            if not isinstance(data, dict):
                raise ValueError("state root must be object")
        except Exception as e:
            backup_path = state_path + f".corrupt.{int(time.time())}.bak"
            try:
                os.replace(state_path, backup_path)
            except Exception:
                pass
            self._safe_json_dump(state_path, {})
            actions.append(f"state_repaired:{self._truncate(str(e), 120)}")

    def _repair_command_file(self, command_path: str, actions: List[str]):
        self._ensure_parent_dir(command_path)
        if not os.path.exists(command_path):
            return
        try:
            data = self._safe_json_load(command_path)
            if not isinstance(data, (dict, list)):
                raise ValueError("command payload must be dict/list")
        except Exception as e:
            bad_path = command_path + f".bad.{int(time.time())}"
            try:
                os.replace(command_path, bad_path)
            except Exception:
                pass
            self._safe_json_dump(command_path, {"cmd": "resume", "ts": time.time()})
            actions.append(f"command_repaired:{self._truncate(str(e), 120)}")

    def _runtime_json_backup_targets(self):
        root = str(getattr(Config, "root_path", self.root_path) or self.root_path)
        candidates = [
            ("state", str(getattr(Config, "STATE_DB_PATH", os.path.join(root, "runtime", "state_memory.json")) or "")),
            ("trading_command", str(getattr(Config, "TRADING_COMMAND_PATH", os.path.join(root, "runtime", "trading_command.json")) or "")),
            ("trading_status", str(getattr(Config, "TRADING_STATUS_PATH", os.path.join(root, "runtime", "trading_status.json")) or "")),
            ("health_poll", str(getattr(Config, "HEALTH_POLL_PATH", os.path.join(root, "runtime", "health_input.json")) or "")),
            ("device_config", str(getattr(Config, "DEVICE_CONFIG_PATH", os.path.join(root, "config", "devices.json")) or "")),
        ]
        out = []
        for label, path in candidates:
            p = str(path or "").strip()
            if p:
                out.append((label, p))
        return out

    def _backup_runtime_json_files(self, actions: List[str]):
        if not bool(getattr(Config, "SELF_HEAL_CONFIG_BACKUP_ENABLED", True)):
            return
        backup_root = os.path.join(self.runtime_path, "config_backups")
        os.makedirs(backup_root, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        max_files = max(5, int(getattr(Config, "SELF_HEAL_CONFIG_BACKUP_MAX_FILES", 30)))
        retention_days = max(1, int(getattr(Config, "SELF_HEAL_CONFIG_BACKUP_RETENTION_DAYS", 14)))
        retention_cutoff = time.time() - (retention_days * 86400)

        for label, src in self._runtime_json_backup_targets():
            if not os.path.exists(src) or not os.path.isfile(src):
                continue
            ext = os.path.splitext(src)[1] or ".json"
            dst = os.path.join(backup_root, f"{label}_{ts}{ext}.bak")
            try:
                shutil.copy2(src, dst)
                actions.append(f"config_backup:{label}")
            except Exception as e:
                actions.append(f"config_backup_failed:{label}:{self._truncate(str(e), 100)}")

        # Cleanup old backups by retention and max files.
        try:
            entries = []
            for name in os.listdir(backup_root):
                full = os.path.join(backup_root, name)
                if not os.path.isfile(full):
                    continue
                mtime = os.path.getmtime(full)
                if mtime < retention_cutoff:
                    try:
                        os.remove(full)
                        actions.append(f"config_backup_pruned:{name}")
                    except Exception:
                        pass
                    continue
                entries.append((mtime, full))

            if len(entries) > max_files:
                entries.sort(key=lambda item: item[0], reverse=True)
                for _, path in entries[max_files:]:
                    try:
                        os.remove(path)
                        actions.append(f"config_backup_pruned:{os.path.basename(path)}")
                    except Exception:
                        continue
        except Exception as e:
            actions.append(f"config_backup_cleanup_failed:{self._truncate(str(e), 100)}")

    def _iter_config_drift_targets(self) -> List[str]:
        raw = str(getattr(Config, "SELF_HEAL_CONFIG_DRIFT_TARGETS", "") or "")
        targets: List[str] = []
        for item in raw.replace(";", ",").split(","):
            part = str(item or "").strip()
            if not part:
                continue
            targets.append(part)
        # Keep order and uniqueness.
        return list(dict.fromkeys(targets))

    def _resolve_target_path(self, target: str) -> str:
        p = str(target or "").strip()
        if not p:
            return ""
        if os.path.isabs(p):
            return p
        return os.path.join(self.root_path, p)

    def _sha256_file(self, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 64)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _build_config_drift_snapshot(self) -> Dict[str, str]:
        snapshot: Dict[str, str] = {}
        for target in self._iter_config_drift_targets():
            full = self._resolve_target_path(target)
            if not full:
                continue
            key = str(target).replace("\\", "/")
            if not os.path.exists(full) or not os.path.isfile(full):
                snapshot[key] = "missing"
                continue
            try:
                snapshot[key] = self._sha256_file(full)
            except Exception:
                snapshot[key] = "error"
        return snapshot

    def _refresh_config_drift_baseline(self, actions: List[str]) -> Dict:
        baseline_path = str(getattr(Config, "SELF_HEAL_CONFIG_DRIFT_PATH", "") or "").strip()
        if not baseline_path:
            return {"enabled": False}
        snapshot = self._build_config_drift_snapshot()
        payload = {
            "updated_at": self._now_iso(),
            "targets": snapshot,
        }
        self._safe_json_dump(baseline_path, payload)
        actions.append("config_drift_baseline_refreshed")
        report = {
            "enabled": True,
            "baseline_path": baseline_path,
            "targets": list(snapshot.keys()),
            "changed": [],
            "new": [],
            "missing": [],
            "changed_count": 0,
            "baseline_refreshed": True,
        }
        self.last_config_drift = report
        return report

    def _collect_config_drift(self, actions: List[str], warnings: List[str]) -> Dict:
        if not bool(getattr(Config, "SELF_HEAL_CONFIG_DRIFT_ENABLED", True)):
            return {"enabled": False}
        baseline_path = str(getattr(Config, "SELF_HEAL_CONFIG_DRIFT_PATH", "") or "").strip()
        if not baseline_path:
            return {"enabled": False}

        current = self._build_config_drift_snapshot()
        report = {
            "enabled": True,
            "baseline_path": baseline_path,
            "targets": list(current.keys()),
            "changed": [],
            "new": [],
            "missing": [],
            "changed_count": 0,
            "baseline_refreshed": False,
        }

        baseline_targets: Dict[str, str] = {}
        baseline_raw = self._safe_json_load(baseline_path) if os.path.exists(baseline_path) else {}
        if isinstance(baseline_raw, dict):
            baseline_targets = dict(baseline_raw.get("targets") or {})

        if not baseline_targets:
            payload = {"updated_at": self._now_iso(), "targets": current}
            self._safe_json_dump(baseline_path, payload)
            actions.append("config_drift_baseline_created")
            report["baseline_refreshed"] = True
            self.last_config_drift = report
            return report

        for key, current_hash in current.items():
            previous_hash = str(baseline_targets.get(key, "") or "")
            if not previous_hash:
                report["new"].append(key)
                continue
            if current_hash == "missing" and previous_hash != "missing":
                report["missing"].append(key)
                continue
            if current_hash != previous_hash:
                report["changed"].append(key)

        changed_count = len(report["changed"]) + len(report["new"]) + len(report["missing"])
        report["changed_count"] = int(changed_count)
        if report["changed"]:
            warnings.append(f"config_drift_changed:{len(report['changed'])}")
        if report["new"]:
            warnings.append(f"config_drift_new:{len(report['new'])}")
        if report["missing"]:
            warnings.append(f"config_drift_missing:{len(report['missing'])}")
        self.last_config_drift = report
        return report

    def _build_step(self, cmd: str, returncode: int, stdout: str = "", stderr: str = "", **extra) -> Dict:
        step = {
            "cmd": str(cmd or "").strip(),
            "returncode": int(returncode),
            "stdout": self._truncate(stdout),
            "stderr": self._truncate(stderr),
        }
        if extra:
            step.update(extra)
        return step

    def _evaluate_deploy_slo(self, heal_report: Dict) -> Dict:
        enforce = bool(getattr(Config, "SELF_DEPLOY_SLO_ENFORCE", True))
        if not enforce:
            return {"enabled": False, "ok": True, "reason": "", "metrics": {}}

        metrics = {
            "health_score": float((heal_report or {}).get("health_score", 0.0) or 0.0),
            "warning_count": int(len((heal_report or {}).get("warnings") or [])),
            "heal_duration_ms": int((heal_report or {}).get("duration_ms") or 0),
        }
        min_health = float(getattr(Config, "SELF_DEPLOY_SLO_MIN_HEALTH_SCORE", 60.0))
        max_warn = int(getattr(Config, "SELF_DEPLOY_SLO_MAX_WARNING_COUNT", 8))
        max_heal_ms = int(getattr(Config, "SELF_DEPLOY_SLO_MAX_HEAL_DURATION_MS", 8000))

        if metrics["health_score"] < min_health:
            return {
                "enabled": True,
                "ok": False,
                "reason": f"SLO health_score {metrics['health_score']:.1f} < {min_health:.1f}",
                "metrics": metrics,
            }
        if metrics["warning_count"] > max_warn:
            return {
                "enabled": True,
                "ok": False,
                "reason": f"SLO warning_count {metrics['warning_count']} > {max_warn}",
                "metrics": metrics,
            }
        if metrics["heal_duration_ms"] > max_heal_ms:
            return {
                "enabled": True,
                "ok": False,
                "reason": f"SLO heal_duration_ms {metrics['heal_duration_ms']} > {max_heal_ms}",
                "metrics": metrics,
            }
        return {"enabled": True, "ok": True, "reason": "", "metrics": metrics}

    def _run_canary_validation(self, steps: List[Dict]) -> Dict:
        enabled = bool(getattr(Config, "SELF_DEPLOY_CANARY_ENABLED", True))
        if not enabled:
            return {"enabled": False, "ok": True, "reason": ""}

        canary_cmd = str(getattr(Config, "SELF_DEPLOY_CANARY_CMD", "") or "").strip()
        if not canary_cmd:
            steps.append(self._build_step("canary", 0, stderr="SELF_DEPLOY_CANARY_CMD not set; canary skipped"))
            return {"enabled": True, "ok": True, "reason": ""}

        canary_args = self._parse_command_text(canary_cmd)
        if not canary_args:
            steps.append(self._build_step("canary", 1, stderr="Invalid SELF_DEPLOY_CANARY_CMD format"))
            return {"enabled": True, "ok": False, "reason": "invalid canary command"}

        retries = max(1, int(getattr(Config, "SELF_DEPLOY_CANARY_RETRIES", 2)))
        timeout_sec = max(10, int(getattr(Config, "SELF_DEPLOY_CANARY_TIMEOUT_SEC", 90)))
        last_step: Dict = {}
        for attempt in range(1, retries + 1):
            step = self._run_command(canary_args, timeout_sec=timeout_sec)
            step["cmd"] = f"{step.get('cmd', '')}  # canary {attempt}/{retries}"
            steps.append(step)
            last_step = step
            if int(step.get("returncode", 1)) == 0:
                return {"enabled": True, "ok": True, "reason": ""}
        reason = str((last_step or {}).get("stderr") or "canary validation failed")
        return {"enabled": True, "ok": False, "reason": self._truncate(reason, 160)}

    def heal_once(self) -> Dict:
        with self._heal_lock:
            started = time.time()
            actions: List[str] = []
            warnings: List[str] = []

            try:
                os.makedirs(self.runtime_path, exist_ok=True)
                actions.append("runtime_ok")
            except Exception as e:
                warnings.append(f"runtime_mkdir_failed:{e}")

            try:
                self._repair_state_file(getattr(Config, "STATE_DB_PATH", os.path.join(self.runtime_path, "state_memory.json")), actions)
            except Exception as e:
                warnings.append(f"state_check_failed:{e}")

            try:
                self._repair_command_file(getattr(Config, "TRADING_COMMAND_PATH", os.path.join(self.runtime_path, "trading_command.json")), actions)
            except Exception as e:
                warnings.append(f"command_check_failed:{e}")

            try:
                self._cleanup_tmp_files(
                    self.runtime_path,
                    max(60, int(getattr(Config, "SELF_HEAL_TMP_MAX_AGE_SEC", 900))),
                    actions,
                )
            except Exception as e:
                warnings.append(f"tmp_cleanup_failed:{e}")

            try:
                self._backup_runtime_json_files(actions)
            except Exception as e:
                warnings.append(f"config_backup_failed:{e}")

            drift = {}
            try:
                drift = self._collect_config_drift(actions, warnings)
            except Exception as e:
                warnings.append(f"config_drift_failed:{self._truncate(str(e), 120)}")
                drift = {"enabled": False}

            try:
                self._rotate_log_if_needed(
                    os.path.join(self.runtime_path, "trading_bot.log"),
                    int(getattr(Config, "SELF_HEAL_LOG_MAX_MB", 20)),
                    actions,
                )
            except Exception as e:
                warnings.append(f"log_rotate_failed:{e}")

            health = {}
            try:
                health = self._collect_runtime_health(actions, warnings)
            except Exception as e:
                warnings.append(f"health_check_failed:{self._truncate(str(e), 120)}")
                health = {"score": 0.0, "severity": "critical"}
            try:
                changed_count = int((drift or {}).get("changed_count", 0) or 0)
                if changed_count > 0:
                    penalty = min(18.0, float(changed_count) * 3.0)
                    health["score"] = max(0.0, float(health.get("score", 0.0) or 0.0) - penalty)
                    score = float(health["score"])
                    if score < 45.0:
                        health["severity"] = "critical"
                    elif score < 65.0:
                        health["severity"] = "warning"
                    else:
                        health["severity"] = "ok"
            except Exception:
                pass

            report = {
                "ts": self._now_iso(),
                "duration_ms": int((time.time() - started) * 1000),
                "actions": actions,
                "warnings": warnings,
                "health": health,
                "health_score": float((health or {}).get("score", 0.0) or 0.0),
                "health_severity": str((health or {}).get("severity") or "unknown"),
                "config_drift": drift,
            }
            try:
                history = self._append_heal_history(report)
                report["history_size"] = len(history)
                anomaly_msg = self._detect_heal_anomaly(history)
                if anomaly_msg:
                    warnings.append(anomaly_msg)
                    report["anomaly"] = anomaly_msg
            except Exception as e:
                warnings.append(f"heal_history_failed:{self._truncate(str(e), 120)}")

            report["ok"] = not warnings
            self.last_heal_report = report
            return report

    def _run_command(self, args: List[str], timeout_sec: int) -> Dict:
        try:
            proc = subprocess.run(
                args,
                cwd=self.root_path,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            return {
                "cmd": " ".join(args),
                "returncode": proc.returncode,
                "stdout": self._truncate(proc.stdout),
                "stderr": self._truncate(proc.stderr),
            }
        except Exception as e:
            return {
                "cmd": " ".join(args),
                "returncode": -1,
                "stdout": "",
                "stderr": self._truncate(str(e)),
            }

    def _parse_command_text(self, command_text: str) -> List[str]:
        text = str(command_text or "").strip()
        if not text:
            return []
        try:
            return shlex.split(text, posix=(os.name != "nt"))
        except Exception:
            return []

    def _build_validation_commands(self) -> List[List[str]]:
        commands: List[List[str]] = []
        if bool(getattr(Config, "SELF_DEPLOY_VALIDATE_COMPILE", True)):
            raw_targets = str(
                getattr(
                    Config,
                    "SELF_DEPLOY_COMPILE_TARGETS",
                    "apps,core,skills,database,runtime,utils,main.py",
                )
                or ""
            )
            targets = []
            for item in raw_targets.replace(";", ",").split(","):
                part = str(item or "").strip()
                if not part:
                    continue
                path = os.path.join(self.root_path, part)
                if os.path.exists(path):
                    targets.append(part)
            if targets:
                commands.append([sys.executable, "-m", "compileall", *targets])

        if bool(getattr(Config, "SELF_DEPLOY_VALIDATE_TESTS", False)):
            default_test_cmd = f"{sys.executable} -m pytest -q"
            test_cmd_text = str(getattr(Config, "SELF_DEPLOY_VALIDATE_TEST_CMD", default_test_cmd) or "").strip()
            test_args = self._parse_command_text(test_cmd_text)
            if test_args:
                commands.append(test_args)

        extra_cmd_text = str(getattr(Config, "SELF_DEPLOY_VALIDATE_EXTRA_CMD", "") or "").strip()
        extra_args = self._parse_command_text(extra_cmd_text)
        if extra_args:
            commands.append(extra_args)
        return commands

    def _allowed_signature_states(self) -> set:
        raw = str(getattr(Config, "SELF_DEPLOY_ALLOWED_SIGNATURE_STATES", "G,U") or "G,U")
        return {item.strip().upper() for item in raw.split(",") if item.strip()}

    def _verify_commit_signature_step(self, ref: str, timeout_sec: int) -> Dict:
        target_ref = str(ref or "").strip()
        if not target_ref:
            return {
                "cmd": "git-signature-verify",
                "returncode": 1,
                "stdout": "",
                "stderr": "Empty ref for signature verification.",
            }
        probe = self._run_command(
            ["git", "log", "-1", "--pretty=%H %G?", target_ref],
            timeout_sec=timeout_sec,
        )
        probe["cmd"] = f"{probe.get('cmd', '')}  # verify-signature"
        if probe.get("returncode") != 0:
            return probe

        out = str(probe.get("stdout", "") or "").strip()
        parts = out.split()
        sig_state = parts[-1].upper() if parts else ""
        commit_hash = parts[0] if parts else ""
        allowed = self._allowed_signature_states()
        require_signed = bool(getattr(Config, "SELF_DEPLOY_REQUIRE_SIGNED_COMMITS", False))

        if require_signed and sig_state not in allowed:
            probe["returncode"] = 1
            probe["stderr"] = (
                f"Commit signature state '{sig_state or '?'}' not allowed for {target_ref}. "
                f"Allowed states: {sorted(allowed)}"
            )
        else:
            probe["stdout"] = (
                f"ref={target_ref} commit={commit_hash} signature_state={sig_state or '?'} "
                f"allowed={sig_state in allowed}"
            )
        return probe

    def deploy_running(self) -> bool:
        return self._deploy_thread is not None and self._deploy_thread.is_alive()

    def _step_failed_report(
        self,
        started: float,
        steps: List[Dict],
        message: str,
        rollback_hash: str = "",
    ) -> Dict:
        if rollback_hash and bool(getattr(Config, "SELF_DEPLOY_AUTO_ROLLBACK", True)):
            timeout_sec = max(30, int(getattr(Config, "SELF_DEPLOY_CMD_TIMEOUT_SEC", 180)))
            rollback_step = self._run_command(["git", "reset", "--hard", rollback_hash], timeout_sec=timeout_sec)
            rollback_step["cmd"] = f"{rollback_step.get('cmd')}  # rollback"
            steps.append(rollback_step)
            if rollback_step.get("returncode") != 0:
                steps.append(
                    {
                        "cmd": "rollback-note",
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "Automatic rollback failed.",
                    }
                )
        report = {
            "ok": False,
            "ts": self._now_iso(),
            "message": message,
            "steps": steps,
            "duration_ms": int((time.time() - started) * 1000),
        }
        self.last_deploy_report = report
        return report

    def deploy_once(self, pull: bool = True, install: bool = False, restart: bool = False) -> Dict:
        with self._deploy_lock:
            started = time.time()
            if not getattr(Config, "SELF_DEPLOY_ENABLED", False):
                report = {
                    "ok": False,
                    "ts": self._now_iso(),
                    "message": "SELF_DEPLOY_ENABLED=0",
                    "steps": [],
                    "duration_ms": 0,
                }
                self.last_deploy_report = report
                return report

            timeout_sec = max(30, int(getattr(Config, "SELF_DEPLOY_CMD_TIMEOUT_SEC", 180)))
            steps: List[Dict] = []
            rollback_hash = ""
            heal_report: Dict = {}

            if getattr(Config, "SELF_DEPLOY_AUTO_HEAL", True):
                heal_report = self.heal_once()
                steps.append(
                    {
                        "cmd": "self-heal",
                        "returncode": 0 if heal_report.get("ok", False) else 1,
                        "stdout": self._truncate(
                            json.dumps(
                                {
                                    "actions": heal_report.get("actions", []),
                                    "warnings": heal_report.get("warnings", []),
                                },
                                ensure_ascii=False,
                            )
                        ),
                        "stderr": "",
                    }
                )

            if bool(getattr(Config, "SELF_DEPLOY_SLO_ENFORCE", True)):
                if not heal_report:
                    heal_report = dict(self.last_heal_report or {})
                    if not heal_report:
                        heal_report = self.heal_once()
                        steps.append(
                            {
                                "cmd": "self-heal",
                                "returncode": 0 if heal_report.get("ok", False) else 1,
                                "stdout": self._truncate(
                                    json.dumps(
                                        {
                                            "actions": heal_report.get("actions", []),
                                            "warnings": heal_report.get("warnings", []),
                                        },
                                        ensure_ascii=False,
                                    )
                                ),
                                "stderr": "",
                            }
                        )
                slo_pre = self._evaluate_deploy_slo(heal_report)
                steps.append(
                    self._build_step(
                        "slo-gate-pre",
                        0 if slo_pre.get("ok", False) else 1,
                        stdout=json.dumps(slo_pre.get("metrics", {}), ensure_ascii=False),
                        stderr=str(slo_pre.get("reason", "") or ""),
                    )
                )
                if not bool(slo_pre.get("ok", False)):
                    return self._step_failed_report(
                        started,
                        steps,
                        f"deploy aborted by pre-SLO gate: {slo_pre.get('reason', 'unknown')}",
                        rollback_hash=rollback_hash,
                    )

            preflight_cmd = self._run_command(["git", "rev-parse", "--is-inside-work-tree"], timeout_sec=timeout_sec)
            steps.append(preflight_cmd)
            if preflight_cmd["returncode"] != 0:
                return self._step_failed_report(started, steps, "deploy preflight failed: not a git worktree")
            if str(preflight_cmd.get("stdout", "")).strip().lower() != "true":
                return self._step_failed_report(started, steps, "deploy preflight failed: repository not detected")

            head_cmd = self._run_command(["git", "rev-parse", "HEAD"], timeout_sec=timeout_sec)
            steps.append(head_cmd)
            if head_cmd["returncode"] == 0:
                rollback_hash = str(head_cmd.get("stdout", "") or "").strip()

            if getattr(Config, "SELF_DEPLOY_REQUIRE_CLEAN_TREE", True):
                dirty_cmd = self._run_command(["git", "status", "--porcelain"], timeout_sec=timeout_sec)
                steps.append(dirty_cmd)
                if dirty_cmd["returncode"] != 0:
                    return self._step_failed_report(
                        started,
                        steps,
                        "deploy preflight failed: cannot inspect git status",
                        rollback_hash=rollback_hash,
                    )
                if str(dirty_cmd.get("stdout", "")).strip():
                    return self._step_failed_report(
                        started,
                        steps,
                        "deploy aborted: working tree is dirty; commit/stash changes first or disable SELF_DEPLOY_REQUIRE_CLEAN_TREE",
                        rollback_hash=rollback_hash,
                    )

            commands: List[List[str]] = []

            if pull:
                branch = str(getattr(Config, "SELF_DEPLOY_BRANCH", "") or "").strip()
                commands.append(["git", "fetch", "--all", "--prune"])

            if install:
                requirements_file = str(
                    getattr(Config, "SELF_DEPLOY_REQUIREMENTS_FILE", "")
                    or ("requirements.txt" if os.path.exists("requirements.txt") else "requirements.in")
                )
                install_cmd = [sys.executable, "-m", "pip", "install", "-r", requirements_file]
                if bool(getattr(Config, "SELF_DEPLOY_REQUIRE_HASHES", False)):
                    install_cmd.insert(4, "--require-hashes")
                commands.append(install_cmd)

            for cmd in commands:
                step = self._run_command(cmd, timeout_sec=timeout_sec)
                steps.append(step)
                if step["returncode"] != 0:
                    return self._step_failed_report(
                        started,
                        steps,
                        f"deploy step failed: {step['cmd']}",
                        rollback_hash=rollback_hash,
                    )

            if pull:
                branch = str(getattr(Config, "SELF_DEPLOY_BRANCH", "") or "").strip()
                if branch:
                    step = self._run_command(["git", "checkout", branch], timeout_sec=timeout_sec)
                    steps.append(step)
                    if step["returncode"] != 0:
                        return self._step_failed_report(
                            started,
                            steps,
                            f"deploy step failed: {step['cmd']}",
                            rollback_hash=rollback_hash,
                        )
                    verify_ref = f"origin/{branch}"
                else:
                    current_branch = self._run_command(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        timeout_sec=timeout_sec,
                    )
                    steps.append(current_branch)
                    if current_branch["returncode"] != 0:
                        return self._step_failed_report(
                            started,
                            steps,
                            "deploy preflight failed: cannot detect current branch",
                            rollback_hash=rollback_hash,
                        )
                    branch_name = str(current_branch.get("stdout", "") or "").strip()
                    verify_ref = f"origin/{branch_name}" if branch_name and branch_name != "HEAD" else "HEAD"

                verify_step = self._verify_commit_signature_step(verify_ref, timeout_sec=timeout_sec)
                steps.append(verify_step)
                if verify_step["returncode"] != 0:
                    return self._step_failed_report(
                        started,
                        steps,
                        "deploy aborted: upstream commit signature verification failed",
                        rollback_hash=rollback_hash,
                    )

                pull_step = self._run_command(["git", "pull", "--ff-only"], timeout_sec=timeout_sec)
                steps.append(pull_step)
                if pull_step["returncode"] != 0:
                    return self._step_failed_report(
                        started,
                        steps,
                        f"deploy step failed: {pull_step['cmd']}",
                        rollback_hash=rollback_hash,
                    )

                head_verify = self._verify_commit_signature_step("HEAD", timeout_sec=timeout_sec)
                steps.append(head_verify)
                if head_verify["returncode"] != 0:
                    return self._step_failed_report(
                        started,
                        steps,
                        "deploy aborted: local HEAD signature verification failed after pull",
                        rollback_hash=rollback_hash,
                    )

            validation_commands = self._build_validation_commands()
            for cmd in validation_commands:
                step = self._run_command(cmd, timeout_sec=timeout_sec)
                steps.append(step)
                if step["returncode"] != 0:
                    return self._step_failed_report(
                        started,
                        steps,
                        (
                            "deploy validation failed; restart dibatalkan "
                            f"({step['cmd']})."
                        ),
                        rollback_hash=rollback_hash,
                    )

            canary = self._run_canary_validation(steps)
            if not bool(canary.get("ok", False)):
                return self._step_failed_report(
                    started,
                    steps,
                    f"deploy canary failed: {canary.get('reason', 'unknown')}",
                    rollback_hash=rollback_hash,
                )

            if restart:
                restart_cmd = str(getattr(Config, "SELF_DEPLOY_RESTART_CMD", "") or "").strip()
                if restart_cmd:
                    restart_args = shlex.split(restart_cmd, posix=(os.name != "nt"))
                    step = self._run_command(restart_args, timeout_sec=timeout_sec)
                    steps.append(step)
                    if step["returncode"] != 0:
                        return self._step_failed_report(
                            started,
                            steps,
                            f"restart failed: {step['cmd']}",
                            rollback_hash=rollback_hash,
                        )
                else:
                    steps.append(
                        {
                            "cmd": "restart",
                            "returncode": 0,
                            "stdout": "",
                            "stderr": "SELF_DEPLOY_RESTART_CMD not set; restart skipped",
                        }
                    )

            post_heal = self.heal_once()
            steps.append(
                {
                    "cmd": "self-heal-post",
                    "returncode": 0 if post_heal.get("ok", False) else 1,
                    "stdout": self._truncate(
                        json.dumps(
                            {
                                "actions": post_heal.get("actions", []),
                                "warnings": post_heal.get("warnings", []),
                            },
                            ensure_ascii=False,
                        )
                    ),
                    "stderr": "",
                }
            )
            if bool(getattr(Config, "SELF_DEPLOY_SLO_ENFORCE", True)):
                slo_post = self._evaluate_deploy_slo(post_heal)
                steps.append(
                    self._build_step(
                        "slo-gate-post",
                        0 if slo_post.get("ok", False) else 1,
                        stdout=json.dumps(slo_post.get("metrics", {}), ensure_ascii=False),
                        stderr=str(slo_post.get("reason", "") or ""),
                    )
                )
                if not bool(slo_post.get("ok", False)):
                    return self._step_failed_report(
                        started,
                        steps,
                        f"deploy aborted by post-SLO gate: {slo_post.get('reason', 'unknown')}",
                        rollback_hash=rollback_hash,
                    )

            try:
                drift_refresh = self._refresh_config_drift_baseline(actions=[])
                steps.append(
                    self._build_step(
                        "config-drift-baseline-refresh",
                        0,
                        stdout=json.dumps({"targets": drift_refresh.get("targets", [])}, ensure_ascii=False),
                    )
                )
            except Exception as e:
                steps.append(
                    self._build_step(
                        "config-drift-baseline-refresh",
                        1,
                        stderr=f"failed: {self._truncate(str(e), 140)}",
                    )
                )

            report = {
                "ok": True,
                "ts": self._now_iso(),
                "message": "deploy completed",
                "steps": steps,
                "duration_ms": int((time.time() - started) * 1000),
            }
            self.last_deploy_report = report
            return report

    def _deploy_worker(self, pull: bool, install: bool, restart: bool):
        try:
            self.deploy_once(pull=pull, install=install, restart=restart)
        except Exception as e:
            self.last_deploy_report = {
                "ok": False,
                "ts": self._now_iso(),
                "message": f"deploy worker crashed: {self._truncate(str(e))}",
                "steps": [],
                "duration_ms": 0,
            }
            log.error(f"Self deploy worker crashed: {e}")

    def deploy_in_background(self, pull: bool = True, install: bool = False, restart: bool = False) -> Dict:
        if self.deploy_running():
            return {
                "started": False,
                "message": "deploy already running",
                "last_report": self.last_deploy_report,
            }
        self._deploy_thread = threading.Thread(
            target=self._deploy_worker,
            args=(pull, install, restart),
            daemon=True,
            name="SelfDeployWorker",
        )
        self._deploy_thread.start()
        return {"started": True, "message": "deploy started"}

