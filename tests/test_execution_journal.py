import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config

_IMPORT_SKIP_REASON = ""
try:
    from database.database_manager import DatabaseManager
    from database.models import ExecutionJournal
except ModuleNotFoundError as exc:
    if exc.name != "flask":
        raise
    DatabaseManager = None
    ExecutionJournal = None
    _IMPORT_SKIP_REASON = "Flask is not installed in this environment"


@unittest.skipIf(bool(_IMPORT_SKIP_REASON), _IMPORT_SKIP_REASON)
class ExecutionJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="elara_test_execution_journal_")
        cls._db_path = os.path.join(cls._tmpdir, "journal.sqlite")
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

    def test_claim_trade_execution_dedups_execution_and_client_order_ids(self):
        manager = DatabaseManager()
        self.assertTrue(manager.ensure_tables())

        self.assertTrue(
            manager.claim_trade_execution(
                "exec-1",
                command_id="cmd-1",
                decision_id="dec-1",
                client_order_id="client-1",
                cmd="limit_order",
                payload={"symbol": "BTC/USDT"},
            )
        )
        self.assertFalse(manager.claim_trade_execution("exec-1", client_order_id="client-2", cmd="limit_order"))
        self.assertFalse(manager.claim_trade_execution("exec-2", client_order_id="client-1", cmd="limit_order"))
        self.assertTrue(
            manager.mark_trade_execution_status(
                "exec-1",
                "submitted",
                payload={"exchange_order_id": "oid-1"},
            )
        )

        with manager.app.app_context():
            row = ExecutionJournal.query.filter_by(execution_id="exec-1").first()
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "submitted")
            self.assertIn("oid-1", row.payload_json)

        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
