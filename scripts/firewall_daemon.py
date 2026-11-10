import os
import re
import time
import logging
import platform
import subprocess

from config.settings import Config

log = logging.getLogger("elara.firewall_daemon")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

IP_REGEX = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

QUEUE_PATH = Config.FIREWALL_QUEUE_PATH
WORK_PATH = QUEUE_PATH + ".work"
POLL_INTERVAL = int(os.getenv("FIREWALL_POLL_INTERVAL", "2"))


def _drain_queue():
    if not os.path.exists(QUEUE_PATH):
        return []
    try:
        os.replace(QUEUE_PATH, WORK_PATH)
    except OSError:
        return []

    try:
        with open(WORK_PATH, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
    finally:
        try:
            os.remove(WORK_PATH)
        except OSError as e:
            log.warning(f"Gagal hapus file kerja firewall queue {WORK_PATH}: {e}")

    seen = set()
    ips = []
    for line in lines:
        if not line or not IP_REGEX.match(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        ips.append(line)
    return ips


def _apply_block(ip: str) -> bool:
    cmd = ["iptables", "-I", "INPUT", "1", "-s", ip, "-j", "DROP"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"iptables error for {ip}: {e}")
    except Exception as e:
        log.error(f"Firewall exception for {ip}: {e}")
    return False


def main():
    if platform.system() != "Linux":
        log.error("Firewall daemon hanya didukung di Linux.")
        return

    log.info(f"Firewall daemon aktif. Queue: {QUEUE_PATH}")
    os.makedirs(os.path.dirname(QUEUE_PATH) or ".", exist_ok=True)

    blocked_cache = set()
    while True:
        for ip in _drain_queue():
            if ip in blocked_cache:
                continue
            if _apply_block(ip):
                blocked_cache.add(ip)
                log.info(f"Blocked IP: {ip}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

