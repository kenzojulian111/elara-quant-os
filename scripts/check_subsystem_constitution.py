from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap.subsystem_constitution import (
    SubsystemConstitutionChecker,
    default_subsystem_constitutions,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Elara subsystem constitution contracts.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-on-critical", action="store_true", help="Return non-zero when contracts fail.")
    args = parser.parse_args()

    report = SubsystemConstitutionChecker(args.root).build_report()
    if args.json:
        payload = {
            **report.to_dict(),
            "subsystems": tuple(item.to_dict() for item in default_subsystem_constitutions()),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Subsystem constitution: {report.status} checked={report.checked}")
        for finding in report.findings:
            print(f"- [critical] {finding}")

    if args.fail_on_critical and not report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
