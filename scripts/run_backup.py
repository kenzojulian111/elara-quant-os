import sys
from utils.backup import run_backup


if __name__ == "__main__":
    ok = run_backup()
    sys.exit(0 if ok else 1)

