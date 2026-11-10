import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.runtime_role import apply_role_defaults

apply_role_defaults("trader")

from core.trader_bootstrap import main as run_trader


if __name__ == "__main__":
    run_trader()

