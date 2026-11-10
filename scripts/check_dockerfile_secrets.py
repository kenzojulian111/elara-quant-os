import re
from pathlib import Path


SECRET_ARG_RE = re.compile(
    r"^\s*ARG\s+[A-Za-z_][A-Za-z0-9_]*?(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)\b",
    re.IGNORECASE,
)


def scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return [f"{path}: read error ({exc})"]
    for idx, line in enumerate(lines, start=1):
        if SECRET_ARG_RE.search(line):
            findings.append(f"{path}:{idx}: build-time secret ARG is forbidden -> {line.strip()}")
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dockerfiles = [root / "Dockerfile"] + sorted(root.rglob("Dockerfile.*"))
    findings: list[str] = []
    for dockerfile in dockerfiles:
        if dockerfile.exists():
            findings.extend(scan_file(dockerfile))
    if findings:
        for item in findings:
            print(item)
        return 1
    print("Dockerfile secret-ARG check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

