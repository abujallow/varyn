from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tmp_data_dir(monkeypatch):
    """Isolated temp directory for tests that touch DATA_DIR-relative paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("VARYN_DATA_DIR", tmpdir)
        yield Path(tmpdir)
