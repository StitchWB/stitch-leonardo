"""Shared fixtures: temp data dir with crypto init + sys.path bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from stitch_leonardo import crypto, storage  # noqa: E402
from tests.mock_leonardo import MockLeonardo  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    crypto.init_crypto(str(d))
    return d


@pytest.fixture()
def db_path(data_dir: Path) -> str:
    path = str(data_dir / "plugin.db")
    storage.migrate(path)
    return path


@pytest.fixture()
def mock():
    server = MockLeonardo()
    yield server
    server.close()
