import os
import zipfile
import logging
import sqlite3
import subprocess
import shutil
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote

from config.settings import Config

log = logging.getLogger("elara.backup")


def _ensure_dir(path: str):
    if path:
        os.makedirs(path, exist_ok=True)


def _remove_file_quietly(path: str):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _add_path_to_zip(zf, base_path, arc_prefix=""):
    if not os.path.exists(base_path):
        return
    if os.path.isfile(base_path):
        arcname = os.path.join(arc_prefix, os.path.basename(base_path))
        zf.write(base_path, arcname=arcname)
        return
    for root, _, files in os.walk(base_path):
        for name in files:
            full_path = os.path.join(root, name)
            rel = os.path.relpath(full_path, base_path)
            arcname = os.path.join(arc_prefix, rel)
            zf.write(full_path, arcname=arcname)


def _pg_dsn():
    raw = str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "") or "").strip()
    if not raw:
        return None
    normalized = raw
    if normalized.startswith("postgresql+psycopg2://"):
        normalized = "postgresql://" + normalized.split("://", 1)[1]
    parsed = urlparse(normalized)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return None

    database = str(parsed.path or "").lstrip("/")
    if not database:
        return None

    return {
        "host": parsed.hostname or "localhost",
        "port": int(parsed.port or 5432),
        "database": database,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def _create_postgres_dump(backup_dir: str, ts: str) -> str:
    dsn = _pg_dsn()
    if not dsn:
        return ""

    dump_path = os.path.join(backup_dir, f"postgres_dump_{ts}.sql")
    cmd = [
        "pg_dump",
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        "--host",
        dsn["host"],
        "--port",
        str(dsn["port"]),
        "--dbname",
        dsn["database"],
        "--file",
        dump_path,
    ]
    if dsn["username"]:
        cmd.extend(["--username", dsn["username"]])

    env = os.environ.copy()
    if dsn["password"]:
        env["PGPASSWORD"] = dsn["password"]

    timeout_sec = max(10, int(getattr(Config, "BACKUP_PG_DUMP_TIMEOUT_SEC", 120)))
    if not shutil.which("pg_dump"):
        log.error("pg_dump executable not found in PATH.")
        return ""
    try:
        subprocess.run(
            cmd,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
        )
    except FileNotFoundError:
        log.error("pg_dump not found. Install PostgreSQL client tools or use DB-side backup.")
        _remove_file_quietly(dump_path)
        return ""
    except subprocess.TimeoutExpired:
        log.error(f"PostgreSQL dump timed out after {timeout_sec}s.")
        _remove_file_quietly(dump_path)
        return ""
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or "").strip()
        log.error(f"PostgreSQL dump failed: {msg}")
        _remove_file_quietly(dump_path)
        return ""
    except Exception as e:
        log.error(f"PostgreSQL dump failed: {e}")
        _remove_file_quietly(dump_path)
        return ""

    if not os.path.exists(dump_path):
        log.error("PostgreSQL dump did not produce an output file.")
        return ""
    return dump_path


def create_backup_archive() -> str:
    backup_dir = Config.BACKUP_LOCAL_DIR
    _ensure_dir(backup_dir)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    archive_name = f"elara_backup_{ts}.zip"
    archive_path = os.path.join(backup_dir, archive_name)

    sources = []
    temp_files = []

    pg_dump = _create_postgres_dump(backup_dir, ts)
    if pg_dump:
        temp_files.append(pg_dump)
        sources.append((pg_dump, "db"))
    else:
        db_path = os.path.join(Config.root_path, "elara.db")
        db_snapshot = ""
        if os.path.exists(db_path):
            try:
                snapshot_name = f"elara_snapshot_{ts}.db"
                db_snapshot = os.path.join(backup_dir, snapshot_name)
                with sqlite3.connect(db_path) as src, sqlite3.connect(db_snapshot) as dst:
                    src.backup(dst)
                temp_files.append(db_snapshot)
                sources.append((db_snapshot, "db"))
            except Exception as e:
                log.error(f"DB snapshot failed: {e}")
                sources.append((db_path, "db"))
    brain_path = Config.BRAIN_STORAGE_PATH
    if os.path.exists(brain_path):
        sources.append((brain_path, "brain_storage"))
    state_path = os.path.join(Config.root_path, "state_memory.json")
    if os.path.exists(state_path):
        sources.append((state_path, "state"))
    config_dir = os.path.join(Config.root_path, "config")
    if os.path.exists(config_dir):
        sources.append((config_dir, "config"))
    devices_path = str(getattr(Config, "DEVICE_CONFIG_PATH", getattr(Config, "DEVICES_JSON_PATH", "")))
    if devices_path and os.path.exists(devices_path):
        sources.append((devices_path, "devices"))

    if not sources:
        log.warning("No backup sources found.")
        return ""

    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, prefix in sources:
                _add_path_to_zip(zf, path, arc_prefix=prefix)
        log.info(f"Backup created: {archive_path}")
        return archive_path
    finally:
        for path in temp_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                log.warning(f"Failed removing temporary backup artifact '{path}': {e}")


def _cleanup_old_backups():
    retention_days = max(1, int(Config.BACKUP_RETENTION_DAYS))
    backup_dir = Config.BACKUP_LOCAL_DIR
    if not os.path.isdir(backup_dir):
        return
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    for name in os.listdir(backup_dir):
        if not name.endswith(".zip"):
            continue
        full_path = os.path.join(backup_dir, name)
        try:
            mtime = datetime.utcfromtimestamp(os.path.getmtime(full_path))
            if mtime < cutoff:
                os.remove(full_path)
        except Exception:
            continue


def _get_drive_service():
    creds_path = Config.GOOGLE_APPLICATION_CREDENTIALS
    if not creds_path or not os.path.exists(creds_path):
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        log.error(f"Drive client init error: {e}")
        return None


def upload_backup_to_drive(archive_path: str) -> bool:
    if not archive_path:
        return False
    service = _get_drive_service()
    if not service:
        return False
    try:
        from googleapiclient.http import MediaFileUpload
        file_metadata: dict = {"name": os.path.basename(archive_path)}
        if Config.BACKUP_GDRIVE_FOLDER_ID:
            file_metadata["parents"] = [Config.BACKUP_GDRIVE_FOLDER_ID]
        media = MediaFileUpload(archive_path, resumable=True)
        created = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        file_id = str((created or {}).get("id") or "").strip()
        if file_id:
            _apply_drive_permissions(service, file_id)
        log.info("Backup uploaded to Google Drive.")
        return True
    except Exception as e:
        log.error(f"Upload failed: {e}")
        return False


def _apply_drive_permissions(service, file_id: str):
    share_type = str(getattr(Config, "BACKUP_GDRIVE_SHARE_TYPE", "none") or "none").strip().lower()
    if share_type in {"", "none", "private"}:
        return

    role = str(getattr(Config, "BACKUP_GDRIVE_SHARE_ROLE", "reader") or "reader").strip().lower()
    role = role if role in {"reader", "commenter", "writer"} else "reader"
    allow_discovery = bool(getattr(Config, "BACKUP_GDRIVE_SHARE_ALLOW_DISCOVERY", False))
    payload: dict = {"type": share_type, "role": role}

    if share_type == "user":
        email = str(getattr(Config, "BACKUP_GDRIVE_SHARE_EMAIL", "") or "").strip()
        if not email:
            log.warning("BACKUP_GDRIVE_SHARE_TYPE=user tapi BACKUP_GDRIVE_SHARE_EMAIL kosong. Skip ACL.")
            return
        payload["emailAddress"] = email
    elif share_type == "domain":
        domain = str(getattr(Config, "BACKUP_GDRIVE_SHARE_DOMAIN", "") or "").strip()
        if not domain:
            log.warning("BACKUP_GDRIVE_SHARE_TYPE=domain tapi BACKUP_GDRIVE_SHARE_DOMAIN kosong. Skip ACL.")
            return
        payload["domain"] = domain
        payload["allowFileDiscovery"] = allow_discovery  # type: ignore
    elif share_type == "anyone":
        payload["allowFileDiscovery"] = allow_discovery  # type: ignore
    else:
        log.warning(f"Unknown BACKUP_GDRIVE_SHARE_TYPE: {share_type}. Skip ACL.")
        return

    try:
        service.permissions().create(
            fileId=file_id,
            body=payload,
            sendNotificationEmail=False,
            fields="id",
        ).execute()
        log.info(
            "Backup ACL applied: file_id=%s type=%s role=%s discoverable=%s",
            file_id,
            share_type,
            role,
            allow_discovery,
        )
    except Exception as e:
        log.error(f"Gagal set ACL backup file {file_id}: {e}")


def run_backup():
    if not Config.BACKUP_ENABLED:
        return False
    archive_path = create_backup_archive()
    if archive_path:
        upload_backup_to_drive(archive_path)
        _cleanup_old_backups()
        return True
    return False

