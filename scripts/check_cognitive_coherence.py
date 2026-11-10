from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap import CognitiveCoherenceChecker


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Elara cognitive coherence contracts.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-on-warning", action="store_true", help="Return non-zero when warnings exist.")
    parser.add_argument("--fail-on-critical", action="store_true", help="Return non-zero when critical findings exist.")
    args = parser.parse_args()

    report = CognitiveCoherenceChecker(args.root).build_static_report()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Cognitive coherence: {report.status} score={report.score}")
        print(
            "Layers="
            f"{report.metrics.get('layer_count', 0)} "
            f"trace_steps={report.metrics.get('trace_step_count', 0)} "
            f"missing_owners={report.metrics.get('missing_owner_modules', 0)}"
        )
        for finding in report.findings:
            print(f"- [{finding.severity}] {finding.code}: {finding.message}")

    if args.fail_on_warning and report.warning_count:
        return 1
    if args.fail_on_critical and report.critical_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
