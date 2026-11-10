from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Config
from trading_core.coordination import RedisStreamsExternalBusAdapter
from trading_core.evaluation import ReplayDatasetStore
from utils.redis_helper import get_redis


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a verified replay dataset from Redis Streams.")
    parser.add_argument("--output", required=True, help="Target JSONL dataset path.")
    parser.add_argument("--stream-key", default="", help="Redis stream key override.")
    parser.add_argument("--after-offset", default="", help="Export events after this Redis stream ID.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum events to export; zero exports all.")
    args = parser.parse_args()

    stream_key = str(
        args.stream_key
        or getattr(Config, "REDIS_STREAM_EVENT_KEY", "")
        or f"{getattr(Config, 'REDIS_PREFIX', 'elara:')}streams:trade_signal"
    ).strip()
    redis_client = get_redis(require=True, purpose="replay_dataset_export")
    bus = RedisStreamsExternalBusAdapter(
        redis_client,
        stream_key=stream_key,
        maxlen=max(100, int(getattr(Config, "REDIS_STREAM_EVENT_MAXLEN", 100_000) or 100_000)),
    )
    events = bus.replay(after_offset=args.after_offset, limit=max(0, int(args.limit)))
    manifest = ReplayDatasetStore().write(args.output, events)
    ReplayDatasetStore().load_verified(args.output)
    print(
        f"Replay dataset exported: events={manifest.event_count} "
        f"sha256={manifest.events_sha256} output={Path(args.output).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
