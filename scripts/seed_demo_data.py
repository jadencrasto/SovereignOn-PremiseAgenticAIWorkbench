"""Report demo seeding as blocked while authorized ingestion is unavailable.

No files are staged and no model or vector backend is started. A future seeder
needs an authenticated operator context and a reviewed ingestion integration.
"""
from __future__ import annotations

import sys

REQUIRED_DOCUMENTS = 3


def seed_data() -> int:
    """Return a failing exit status until a trusted seeding path exists."""
    print('Demo seeding blocked: authorized document ingestion is unavailable.')
    print(f'Documents: attempted=0 ingested=0 failed=0 skipped={REQUIRED_DOCUMENTS}')
    print('Required seeding did not complete.')
    return 1


if __name__ == '__main__':
    sys.exit(seed_data())
