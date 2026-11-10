import argparse
import py_compile
import subprocess
import sys
from pathlib import Path


EXCLUDE_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "brain_storage",
    "documents",
    "runtime",
    "instance",
}


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if any(part in EXCLUDE_PARTS for part in path.parts):
            continue
        yield path


def compile_one_by_one(root: Path) -> list[tuple[Path, str]]:
    failures: list[tuple[Path, str]] = []
    for path in _iter_python_files(root):
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append((path, str(exc)))
    return failures


def run_tests_one_by_one(root: Path) -> list[tuple[str, int, str, str]]:
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return []
    failures: list[tuple[str, int, str, str]] = []
    for file in sorted(tests_dir.glob("test_*.py")):
        cmd = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            file.name,
        ]
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            failures.append((file.name, proc.returncode, proc.stdout, proc.stderr))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep Python checks: per-file compile + per-test-file unittest.")
    parser.add_argument(
        "--root",
        default=".",
        help="Project root folder (default: current working directory).",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()

    print(f"[check] root={root}")

    compile_failures = compile_one_by_one(root)
    print(f"[compile] checked={len(list(_iter_python_files(root)))} failed={len(compile_failures)}")
    for path, msg in compile_failures:
        print(f"[compile][FAIL] {path.relative_to(root)} :: {msg}")

    test_failures = run_tests_one_by_one(root)
    tests_count = len(list((root / "tests").glob("test_*.py"))) if (root / "tests").exists() else 0
    print(f"[tests] checked={tests_count} failed={len(test_failures)}")
    for name, code, out, err in test_failures:
        print(f"[tests][FAIL] {name} rc={code}")
        if out.strip():
            print(out)
        if err.strip():
            print(err)

    if compile_failures or test_failures:
        return 1
    print("[result] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

