from __future__ import annotations

import os
import sys
from pathlib import Path


# Ensure src-layout imports work in local dev/test without an editable install.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Keep tests hermetic: in-memory DB + no background poller.
os.environ.setdefault("GOVGRAPH_DB_PATH", ":memory:")
os.environ.setdefault("GOVGRAPH_ENABLE_POLLER", "false")
