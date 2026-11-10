import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from database.database_manager import DatabaseManager
from database.models import UserSession, db


class DatabaseManagerSessionRevocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_db_revoke_")
        cls._db_path = os.path.join(cls._tmpdir, "revoke.sqlite")
        cls._cfg_backup = {
            "SQLALCHEMY_DATABASE_URI": getattr(Config, "SQLALCHEMY_DATABASE_URI", ""),
            "SQLALCHEMY_TRACK_MODIFICATIONS": getattr(Config, "SQLALCHEMY_TRACK_MODIFICATIONS", False),
            "DB_AUTO_CREATE_TABLES": getattr(Config, "DB_AUTO_CREATE_TABLES", True),
        }
        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{cls._db_path}"
        Config.SQLALCHEMY_TRACK_MODIFICATIONS = False
        Config.DB_AUTO_CREATE_TABLES = True

        DatabaseManager._headless_shared_app = None
        cls.manager = DatabaseManager()
        cls.manager.ensure_tables()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.manager.shutdown()
        except Exception:
            pass
        DatabaseManager._headless_shared_app = None
        for key, value in cls._cfg_backup.items():
            setattr(Config, key, value)
        try:
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def setUp(self):
        with self.manager._ctx():
            db.session.query(UserSession).delete(synchronize_session=False)
            db.session.commit()
        self.assertTrue(
            self.manager.register_user_session(username="alice@test.local", jti="alice-jti-1")
        )
        self.assertTrue(
            self.manager.register_user_session(username="bob@test.local", jti="bob-jti-1")
        )

    def test_wildcard_username_does_not_revoke_all_sessions(self):
        revoked = self.manager.revoke_user_sessions(username="%")
        self.assertEqual(revoked, [])
        self.assertFalse(self.manager.is_user_session_revoked("alice-jti-1"))
        self.assertFalse(self.manager.is_user_session_revoked("bob-jti-1"))

    def test_revoke_username_case_insensitive_exact_match(self):
        revoked = self.manager.revoke_user_sessions(username="ALICE@TEST.LOCAL")
        revoked_jtis = {str(row.get("jti") or "") for row in revoked}
        self.assertEqual(revoked_jtis, {"alice-jti-1"})
        self.assertTrue(self.manager.is_user_session_revoked("alice-jti-1"))
        self.assertFalse(self.manager.is_user_session_revoked("bob-jti-1"))


if __name__ == "__main__":
    unittest.main()

