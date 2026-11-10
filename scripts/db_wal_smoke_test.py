import sys
import threading
import time
from pathlib import Path
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Config
from database.models import db, CommandLog


def create_app(db_path: str):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = Config.SQLALCHEMY_ENGINE_OPTIONS
    db.init_app(app)
    return app


def worker(app, idx, iterations, results):
    errors = 0
    locked = 0
    with app.app_context():
        for i in range(iterations):
            try:
                entry = CommandLog(
                    user_id=f"worker_{idx}",
                    source="wal_test",
                    raw_text=f"test {idx}-{i}",
                    intent="test",
                    entities_json="[]"
                )
                db.session.add(entry)
                db.session.commit()
            except Exception as e:
                msg = str(e).lower()
                if "database is locked" in msg:
                    locked += 1
                errors += 1
                db.session.rollback()
    results[idx] = (errors, locked)


def main():
    runtime_dir = ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "wal_smoke_test.db"

    if db_path.exists():
        db_path.unlink()

    app = create_app(str(db_path))
    with app.app_context():
        db.create_all()

    threads = 8
    iterations = 60
    results = [None] * threads

    start = time.time()
    workers = []
    for i in range(threads):
        t = threading.Thread(target=worker, args=(app, i, iterations, results))
        t.start()
        workers.append(t)

    for t in workers:
        t.join()

    total_errors = sum(r[0] for r in results if r)
    total_locked = sum(r[1] for r in results if r)
    duration = time.time() - start

    print("DB WAL SMOKE TEST")
    print(f"Threads: {threads}, Iterations: {iterations}")
    print(f"Total Errors: {total_errors}")
    print(f"Locked Errors: {total_locked}")
    print(f"Duration: {duration:.2f}s")


if __name__ == "__main__":
    main()

