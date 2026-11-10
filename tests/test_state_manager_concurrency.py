import os
import tempfile
import time
import unittest
from unittest.mock import patch

import utils.redis_helper as redis_helper
from config.settings import Config
from core.exceptions import StateBackendUnavailableError
from core.state_manager import StateManager


class _FakeRedisPool:
    @staticmethod
    def disconnect():
        return None


class _FakeRedis:
    def __init__(self):
        self._data = {}
        self.connection_pool = _FakeRedisPool()

    def set(self, key, value, px=None):
        _ = px
        self._data[str(key)] = value
        return True

    def get(self, key):
        return self._data.get(str(key))

    def delete(self, key):
        self._data.pop(str(key), None)
        return 1

    def scan_iter(self, match=None):
        prefix = str(match or "").replace("*", "")
        for key in list(self._data.keys()):
            if not match or key.startswith(prefix):
                yield key

    @staticmethod
    def close():
        return None


class StateManagerConcurrencyTests(unittest.TestCase):
    def test_file_backend_cross_instance_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with patch.object(Config, "STATE_BACKEND", "file"):
                first = StateManager(db_path=state_path, save_delay=5.0)
                second = StateManager(db_path=state_path, save_delay=5.0)
                try:
                    first.set_state("profile", {"name": "julia"}, user_id="u1")
                    self.assertEqual(second.get_state("profile", user_id="u1"), {"name": "julia"})

                    second.set_state("profile", {"name": "julia-updated"}, user_id="u1")
                    self.assertEqual(first.get_state("profile", user_id="u1"), {"name": "julia-updated"})

                    second.set_state("temp", "x", user_id="u1", ttl=0.15)
                    time.sleep(0.25)
                    self.assertIsNone(first.get_state("temp", user_id="u1"))
                finally:
                    first.shutdown()
                    second.shutdown()

    def test_redis_fallbacks_to_memory_when_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with (
                patch.object(Config, "STATE_BACKEND", "redis"),
                patch.object(Config, "STATE_BACKEND_FAIL_FAST", False),
                patch.object(Config, "STATE_FALLBACK_MODE", "memory"),
                patch.object(redis_helper, "get_redis", return_value=None),
            ):
                manager = StateManager(db_path=state_path, save_delay=0.0)
                try:
                    manager.set_state("k", "v", user_id="u")
                    self.assertEqual(manager.get_state("k", user_id="u"), "v")
                    self.assertEqual(manager._backend, "memory")
                finally:
                    manager.shutdown()

    def test_redis_unavailable_with_fallback_none_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with (
                patch.object(Config, "STATE_BACKEND", "redis"),
                patch.object(Config, "STATE_BACKEND_FAIL_FAST", False),
                patch.object(Config, "STATE_FALLBACK_MODE", "none"),
                patch.object(redis_helper, "get_redis", return_value=None),
            ):
                with self.assertRaises(StateBackendUnavailableError):
                    StateManager(db_path=state_path, save_delay=0.0)

    def test_redis_fail_fast_raises_when_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with (
                patch.object(Config, "STATE_BACKEND", "redis"),
                patch.object(Config, "STATE_BACKEND_FAIL_FAST", True),
                patch.object(redis_helper, "get_redis", return_value=None),
            ):
                with self.assertRaises(StateBackendUnavailableError):
                    StateManager(db_path=state_path, save_delay=0.0)

    def test_file_backend_supports_encrypted_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with (
                patch.object(Config, "STATE_BACKEND", "file"),
                patch.object(Config, "STATE_ENCRYPTION_ENABLED", True),
                patch.object(Config, "STATE_ENCRYPTION_KEY", "unit-test-state-key"),
                patch.object(Config, "STATE_ENCRYPTION_REQUIRED", True),
            ):
                manager = StateManager(db_path=state_path, save_delay=0.0)
                try:
                    manager.set_state("secret", {"token": "abc123"}, user_id="u1")
                    with open(state_path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    self.assertNotIn("abc123", raw)
                    self.assertEqual(manager.get_state("secret", user_id="u1"), {"token": "abc123"})
                finally:
                    manager.shutdown()

    def test_redis_dump_respects_explicit_empty_user_id(self):
        fake_redis = _FakeRedis()
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with (
                patch.object(Config, "STATE_BACKEND", "redis"),
                patch.object(Config, "STATE_BACKEND_FAIL_FAST", False),
                patch.object(redis_helper, "get_redis", return_value=fake_redis),
            ):
                manager = StateManager(db_path=state_path, save_delay=0.0)
                try:
                    manager.set_state("k1", "v-empty", user_id="")
                    manager.set_state("k2", "v-user", user_id="u1")
                    dumped = manager.dump(user_id="")
                    self.assertEqual(dumped, {"k1": "v-empty"})
                finally:
                    manager.shutdown()


if __name__ == "__main__":
    unittest.main()

