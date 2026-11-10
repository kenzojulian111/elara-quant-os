import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("ENABLE_TRADING_SKILL", "1")
os.environ.setdefault("ENABLE_TRADING_ORCHESTRATOR", "0")
os.environ.setdefault("APP_ROLE", "core")

from core.app_bootstrap import main as run_main


if __name__ == "__main__":
    run_main()

