from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.protocols import build_core_contract_registry, validate_core_contracts


def main() -> int:
    parser = argparse.ArgumentParser(description="Check frozen Elara core platform contracts.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-on-critical", action="store_true", help="Return non-zero when contracts drift.")
    args = parser.parse_args()

    report = validate_core_contracts()
    if args.json:
        payload = {
            **report.to_dict(),
            "contracts": tuple(contract.to_dict() for contract in build_core_contract_registry()),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Core contracts: {report.status} version={report.version} checked={report.checked}")
        for mismatch in report.mismatches:
            print(f"- [critical] {mismatch}")

    if args.fail_on_critical and not report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
