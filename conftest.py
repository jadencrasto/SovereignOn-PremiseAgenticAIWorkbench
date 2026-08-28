"""
conftest.py (project root)
--------------------------
Pytest configuration — adds the project root to sys.path so that
'import backend.xxx' works from any test file.
"""

import sys
from pathlib import Path

# Insert project root at the beginning of sys.path
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
