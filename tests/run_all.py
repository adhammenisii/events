"""Run every test module in this folder.

    python tests/run_all.py

pytest is not a dependency of this project, so the suite ships with its own
runner. ``pytest tests`` works too if it happens to be installed.
"""

import runpy
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))


def main() -> int:
    modules = sorted(TESTS_DIR.glob("test_*.py"))
    failed = []

    for module in modules:
        print(f"\n{module.name}")
        print("-" * len(module.name))
        try:
            runpy.run_path(str(module), run_name="__main__")
        except SystemExit as exit_status:
            if exit_status.code:
                failed.append(module.name)

    print("\n" + "=" * 46)
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(modules)} test modules passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
