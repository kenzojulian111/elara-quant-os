import unittest

from utils.redis_helper import redis_readiness_probe


class _FakePubSub:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.channel = ""

    def subscribe(self, channel):
        self.channel = channel

    def get_message(self, timeout=0.05):
        _ = timeout
        if not self.redis_client.messages:
            return None
        return {"data": self.redis_client.messages.pop(0)}

    def close(self):
        return None


class _ReadyRedis:
    def __init__(self):
        self.values = {}
        self.messages = []
        self.streams = {}
        self.stream_seq = 0

    def ping(self):
        return True

    def set(self, key, value, ex=None):
        _ = ex
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)
        return 1

    def pubsub(self, ignore_subscribe_messages=True):
        _ = ignore_subscribe_messages
        return _FakePubSub(self)

    def publish(self, channel, value):
        _ = channel
        self.messages.append(value)
        return 1

    def xadd(self, key, fields, maxlen=None, approximate=True):
        _ = maxlen, approximate
        self.stream_seq += 1
        stream_id = f"{self.stream_seq}-0"
        self.streams.setdefault(key, []).append((stream_id, fields))
        return stream_id

    def xrange(self, key, min="-", max="+", count=None):
        _ = min, max
        rows = list(self.streams.get(key, ()))
        return rows[:count] if count else rows

    def xdel(self, key, *stream_ids):
        rows = self.streams.get(key, [])
        kept = [row for row in rows if row[0] not in set(stream_ids)]
        self.streams[key] = kept
        return len(rows) - len(kept)


class RedisReadinessTests(unittest.TestCase):
    def test_readiness_probe_checks_ping_rw_pubsub_streams_and_latency(self):
        probe = redis_readiness_probe(
            _ReadyRedis(),
            redis_prefix="elara:test:",
            latency_threshold_ms=1000,
        )

        self.assertTrue(probe["ok"])
        self.assertEqual(probe["checks"]["ping"]["status"], "ok")
        self.assertEqual(probe["checks"]["read_write"]["status"], "ok")
        self.assertEqual(probe["checks"]["pubsub"]["status"], "ok")
        self.assertEqual(probe["checks"]["streams"]["status"], "ok")
        self.assertEqual(probe["checks"]["latency"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
