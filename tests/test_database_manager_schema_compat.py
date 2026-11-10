import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import create_engine, inspect

from config.settings import Config
from database.database_manager import DatabaseManager


class DatabaseManagerSchemaCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_db_schema_compat_")
        cls._db_path = os.path.join(cls._tmpdir, "legacy.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
        }
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.DB_AUTO_CREATE_TABLES = True

    @classmethod
    def tearDownClass(cls):
        DatabaseManager._headless_shared_app = None
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        DatabaseManager._headless_shared_app = None
        if os.path.exists(self._db_path):
            os.remove(self._db_path)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email VARCHAR(120) NOT NULL UNIQUE,
                    password_hash VARCHAR(256) NOT NULL DEFAULT '',
                    name VARCHAR(80),
                    role VARCHAR(20)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def test_ensure_tables_adds_missing_users_created_at_column(self):
        manager = DatabaseManager()
        self.assertTrue(manager.ensure_tables())

        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        try:
            columns = {col["name"] for col in inspect(engine).get_columns("users")}
        finally:
            engine.dispose()
            manager.shutdown()

        self.assertIn("created_at", columns)


if __name__ == "__main__":
    unittest.main()

