"""Makes the suite importable under pytest as well as standalone.

Each test file imports ``support`` directly, which works when the file is run
as a script because Python puts its own directory on the path. pytest does
not, so this adds it.
"""

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
for path in (TESTS_DIR, TESTS_DIR.parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
