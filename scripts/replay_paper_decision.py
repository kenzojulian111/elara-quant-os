from __future__ import annotations

import argparse

from trading_core.coordination.event_ledger import EventLedger
from trading_core.coordination.paper_audit import PaperAuditRepository
from utils.fast_json import dumps_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay an Elara MVP paper decision from its audit trace ID.")
    parser.add_argument("trace_id")
    parser.add_argument("--ledger", default="runtime/paper_ledger.jsonl")
    args = parser.parse_args()
    replay = PaperAuditRepository(EventLedger(args.ledger)).replay(args.trace_id)
    print(dumps_text(replay.to_dict()))
    return 0 if replay.found and replay.integrity_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
