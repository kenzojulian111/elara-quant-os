from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.roadmap import build_decision_topology


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Elara decision topology as Mermaid.")
    parser.add_argument("--output", help="Optional output file. Prints to stdout when omitted.")
    args = parser.parse_args()

    mermaid = build_decision_topology().to_mermaid()
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(mermaid, encoding="utf-8")
    else:
        print(mermaid, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
