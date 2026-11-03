from collections.abc import Callable
from pathlib import Path

import pytest

from release_notes.config import CONFIG_PATH


@pytest.fixture
def write_release_notes_config(tmp_path: Path) -> Callable[[str], Path]:
    """Write a release-notes config into tmp_path at the consumer config location."""

    def _write(content: str) -> Path:
        path = tmp_path / CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write
