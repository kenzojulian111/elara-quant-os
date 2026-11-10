from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_REDIS_VERSION = "5.0.14.1"


def find_python(project_root: Path) -> str:
    if os.name == "nt":
        candidates = [
            project_root / ".venv" / "Scripts" / "python.exe",
            project_root.parent / ".venv" / "Scripts" / "python.exe",
            project_root / ".venv" / "bin" / "python",
            project_root.parent / ".venv" / "bin" / "python",
        ]
    else:
        candidates = [
            project_root / ".venv" / "bin" / "python",
            project_root.parent / ".venv" / "bin" / "python",
            project_root / ".venv" / "Scripts" / "python.exe",
            project_root.parent / ".venv" / "Scripts" / "python.exe",
        ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable or "python3"


def tcp_port_open(address: str, port: int, timeout_s: float = 0.2) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        try:
            sock.connect((address, port))
        except OSError:
            return False
        return True


def wait_tcp_port(
    *,
    address: str,
    port: int,
    label: str,
    retries: int = 60,
    delay_s: float = 0.25,
) -> None:
    for _ in range(retries):
        if tcp_port_open(address, port):
            return
        time.sleep(delay_s)
    raise RuntimeError(f"Timed out waiting for {label} to listen on {address}:{port}")


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as response, open(tmp, "wb") as handle:
        shutil.copyfileobj(response, handle)
    tmp.replace(dest)


def ensure_redis_binaries(*, redis_dir: Path, version: str) -> None:
    server = redis_dir / "redis-server.exe"
    if server.exists():
        return

    redis_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"Redis-x64-{version}.zip"
    zip_path = redis_dir / zip_name
    url = f"https://github.com/tporadowski/redis/releases/download/v{version}/{zip_name}"

    if not zip_path.exists():
        print(f"Downloading Redis portable ({zip_name})...")
        download(url, zip_path)

    print("Extracting Redis portable...")
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(redis_dir)

    if not server.exists():
        raise RuntimeError(f"Redis binaries missing after extract: {server}")


def redis_ping(*, cli: Path | str, address: str, port: int) -> str:
    completed = subprocess.run(
        [str(cli), "-h", address, "-p", str(port), "ping"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    out = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise RuntimeError(f"redis-cli ping returned code {completed.returncode}: {err or out}")
    return out


def start_redis(
    *,
    address: str,
    port: int,
    logs_dir: Path,
    redis_dir: Path,
    redis_version: str = DEFAULT_REDIS_VERSION,
) -> None:
    if os.name != "nt":
        if tcp_port_open(address, port):
            print(f"Redis already running on {address}:{port}")
            return
        system_server = shutil.which("redis-server")
        system_cli = shutil.which("redis-cli")
        if not system_server:
            print(
                f"Redis binary not installed on Linux; skipping Redis startup on {address}:{port} "
                "(using local file state/IPC fallback)."
            )
            return
        print(f"Starting system Redis on {address}:{port}...")
        stdout_path = logs_dir / "redis_stdout.log"
        stderr_path = logs_dir / "redis_stderr.log"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stdout_path, "w", encoding="utf-8") as out_file, open(stderr_path, "w", encoding="utf-8") as err_file:
            process = subprocess.Popen(
                [system_server, "--bind", address, "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=out_file,
                stderr=err_file,
                close_fds=True,
                start_new_session=True,
            )
        wait_tcp_port(address=address, port=port, label="Redis")
        if system_cli:
            pong = redis_ping(cli=system_cli, address=address, port=port)
            if pong != "PONG":
                raise RuntimeError(f"Redis started (PID {process.pid}) but did not reply to PING (got {pong!r}).")
        print(f"Redis started (PID {process.pid}).")
        return

    ensure_redis_binaries(redis_dir=redis_dir, version=redis_version)

    server = redis_dir / "redis-server.exe"
    cli = redis_dir / "redis-cli.exe"
    conf = redis_dir / "redis.windows.conf"
    if not conf.exists():
        raise RuntimeError(f"Redis config not found: {conf}")

    if tcp_port_open(address, port):
        if cli.exists():
            pong = redis_ping(cli=cli, address=address, port=port)
            if pong != "PONG":
                raise RuntimeError(f"Port {port} is open but Redis did not reply to PING (got {pong!r}).")
        print(f"Redis already running on {address}:{port}")
        return

    print(f"Starting Redis on {address}:{port}...")
    stdout_path = logs_dir / "redis_stdout.log"
    stderr_path = logs_dir / "redis_stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with open(stdout_path, "w", encoding="utf-8") as out_file, open(stderr_path, "w", encoding="utf-8") as err_file:
        process = subprocess.Popen(
            [str(server), str(conf), "--bind", address, "--port", str(port)],
            cwd=str(redis_dir),
            stdin=subprocess.DEVNULL,
            stdout=out_file,
            stderr=err_file,
            creationflags=creationflags,
            close_fds=True,
        )

    wait_tcp_port(address=address, port=port, label="Redis")
    if cli.exists():
        pong = redis_ping(cli=cli, address=address, port=port)
        if pong != "PONG":
            raise RuntimeError(f"Redis started (PID {process.pid}) but did not reply to PING (got {pong!r}).")
    print(f"Redis started (PID {process.pid}).")


def start_python_service(
    *,
    name: str,
    python: str,
    working_directory: Path,
    args: list[str],
    address: str,
    port: int,
    stdout_path: Path,
    stderr_path: Path,
) -> None:
    if tcp_port_open(address, port):
        print(f"{name} already running on {address}:{port}")
        return

    print(f"Starting {name}...")
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with open(stdout_path, "w", encoding="utf-8") as out_file, open(stderr_path, "w", encoding="utf-8") as err_file:
        process = subprocess.Popen(
            [python, *args],
            cwd=str(working_directory),
            stdin=subprocess.DEVNULL,
            stdout=out_file,
            stderr=err_file,
            creationflags=creationflags,
            close_fds=True,
            start_new_session=(os.name != "nt"),
        )

    wait_tcp_port(address=address, port=port, label=name)
    print(f"{name} started (PID {process.pid}).")
