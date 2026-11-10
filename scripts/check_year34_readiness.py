from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap import Year34ReadinessChecker


def main() -> int:
    parser = argparse.ArgumentParser(description="Check bounded Year 3-4 repository baseline readiness.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-below", type=int, default=90, help="Minimum acceptable repository baseline score.")
    args = parser.parse_args()

    report = Year34ReadinessChecker(args.root).build_report()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Year 3-4 repository baseline: score={report.score} ready={report.baseline_ready}")
        for area in report.areas:
            print(f"- {area.area}: score={area.score} missing={len(area.missing_anchors)}")
        print("Production deployment remains separate from repository baseline readiness.")
    return 1 if report.score < int(args.fail_below) else 0


if __name__ == "__main__":
    raise SystemExit(main())
