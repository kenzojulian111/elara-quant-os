from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap import ArchitectureHeatmapBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Elara architecture hotspot heatmap.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--limit", type=int, default=12, help="Number of hotspots to show.")
    args = parser.parse_args()

    heatmap = ArchitectureHeatmapBuilder(args.root).build(limit=args.limit)
    if args.json:
        print(json.dumps(heatmap.to_dict(), indent=2, sort_keys=True))
    else:
        print(heatmap.to_markdown(limit=args.limit), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
