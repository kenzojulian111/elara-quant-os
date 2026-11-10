import types
import unittest
from unittest.mock import patch

from database.database_manager import DatabaseManager


class _FakeLock:
    def __init__(self, fail_before_success=0):
        self.fail_before_success = int(fail_before_success)
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self, blocking=True, timeout=-1):
        _ = (blocking, timeout)
        self.acquire_calls += 1
        return self.acquire_calls > self.fail_before_success

    def release(self):
        self.release_calls += 1


class _FakeSession:
    def __init__(self, commit_side_effect=None):
        self._commit_side_effect = list(commit_side_effect or [])
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self):
        self.commit_calls += 1
        if self._commit_side_effect:
            exc = self._commit_side_effect.pop(0)
            if exc is not None:
                raise exc

    def rollback(self):
        self.rollback_calls += 1


class DatabaseManagerCommitRetryLockTests(unittest.TestCase):
    def test_sqlite_lock_acquire_retries_until_success(self):
        manager = DatabaseManager()
        fake_lock = _FakeLock(fail_before_success=2)
        fake_session = _FakeSession()
        fake_db = types.SimpleNamespace(session=fake_session)

        with patch.object(manager, "_is_sqlite_backend", return_value=True), patch(
            "database.database_manager._DB_WRITE_LOCK", fake_lock
        ), patch("database.database_manager.db", fake_db), patch(
            "database.database_manager.time.sleep", return_value=None
        ), patch.object(
            manager, "_record_db_write_lock_contention_metric"
        ):
            ok = manager._commit_with_retry(max_attempts=3, backoff=0.0)

        self.assertTrue(ok)
        self.assertEqual(fake_lock.acquire_calls, 3)
        self.assertEqual(fake_session.commit_calls, 1)
        self.assertEqual(fake_lock.release_calls, 1)

    def test_sqlite_lock_acquire_failure_returns_false_after_attempts(self):
        manager = DatabaseManager()
        fake_lock = _FakeLock(fail_before_success=99)
        fake_session = _FakeSession()
        fake_db = types.SimpleNamespace(session=fake_session)

        with patch.object(manager, "_is_sqlite_backend", return_value=True), patch(
            "database.database_manager._DB_WRITE_LOCK", fake_lock
        ), patch("database.database_manager.db", fake_db), patch(
            "database.database_manager.time.sleep", return_value=None
        ), patch.object(
            manager, "_record_db_write_lock_contention_metric"
        ) as record_contention_metric:
            ok = manager._commit_with_retry(max_attempts=3, backoff=0.0)

        self.assertFalse(ok)
        self.assertEqual(fake_lock.acquire_calls, 3)
        self.assertEqual(fake_session.commit_calls, 0)
        self.assertEqual(fake_lock.release_calls, 0)
        self.assertEqual(record_contention_metric.call_count, 3)

    def test_sqlite_commit_lock_error_is_retried(self):
        manager = DatabaseManager()
        fake_lock = _FakeLock(fail_before_success=0)
        fake_session = _FakeSession(commit_side_effect=[RuntimeError("database is locked"), None])
        fake_db = types.SimpleNamespace(session=fake_session)

        with patch.object(manager, "_is_sqlite_backend", return_value=True), patch(
            "database.database_manager._DB_WRITE_LOCK", fake_lock
        ), patch("database.database_manager.db", fake_db), patch(
            "database.database_manager.time.sleep", return_value=None
        ), patch.object(
            manager, "_record_db_write_lock_contention_metric"
        ) as record_contention_metric:
            ok = manager._commit_with_retry(max_attempts=3, backoff=0.0)

        self.assertTrue(ok)
        self.assertEqual(fake_session.commit_calls, 2)
        self.assertEqual(fake_session.rollback_calls, 1)
        self.assertEqual(fake_lock.release_calls, 1)
        self.assertEqual(record_contention_metric.call_count, 0)


if __name__ == "__main__":
    unittest.main()

