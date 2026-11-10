import types
import unittest
from unittest.mock import patch

from database.database_manager import DatabaseManager


class _FakeQuery:
    def __init__(self):
        self.filter_kwargs = None
        self.with_for_update_kwargs = None

    def filter_by(self, **kwargs):
        self.filter_kwargs = dict(kwargs)
        return self

    def with_for_update(self, **kwargs):
        self.with_for_update_kwargs = dict(kwargs)
        return self


class _FakeSession:
    def __init__(self, query_obj):
        self._query_obj = query_obj

    def query(self, _model):
        return self._query_obj


class DatabaseManagerDeadlockTests(unittest.TestCase):
    def test_deadlock_detector_covers_nowait_markers(self):
        manager = DatabaseManager()
        self.assertTrue(manager._is_deadlock_error(RuntimeError("could not obtain lock on row in relation trade")))
        self.assertTrue(manager._is_deadlock_error(RuntimeError("ERROR: 55P03 lock not available")))
        self.assertFalse(manager._is_deadlock_error(RuntimeError("network timeout")))

    def test_query_trade_for_update_uses_nowait_on_server_db(self):
        manager = DatabaseManager()
        query = _FakeQuery()
        fake_db = types.SimpleNamespace(session=_FakeSession(query))
        with patch.object(manager, "_is_sqlite_backend", return_value=False), patch(
            "database.database_manager.db", fake_db
        ):
            out = manager._query_trade_for_update(99, nowait=True)
        self.assertIs(out, query)
        self.assertEqual(query.filter_kwargs, {"id": 99})
        self.assertEqual(query.with_for_update_kwargs, {"nowait": True})

    def test_query_trade_for_update_skips_nowait_on_sqlite(self):
        manager = DatabaseManager()
        query = _FakeQuery()
        fake_db = types.SimpleNamespace(session=_FakeSession(query))
        with patch.object(manager, "_is_sqlite_backend", return_value=True), patch(
            "database.database_manager.db", fake_db
        ):
            out = manager._query_trade_for_update(123, nowait=True)
        self.assertIs(out, query)
        self.assertEqual(query.filter_kwargs, {"id": 123})
        self.assertEqual(query.with_for_update_kwargs, {})


if __name__ == "__main__":
    unittest.main()

