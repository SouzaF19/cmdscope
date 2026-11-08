"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from cmdscope.config import (
    Config,
    FilesystemConfig,
    NetworkConfig,
    PrivacyConfig,
    ProcessesConfig,
    ReportConfig,
    StorageConfig,
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "data" / "cmdscope.db"


@pytest.fixture
def fast_config(tmp_path: Path, database: Path) -> Config:
    work = tmp_path / "work"
    work.mkdir()
    return Config(
        filesystem=FilesystemConfig(
            watch=(str(work),),
            poll_interval=0.02,
            native_events=False,
        ),
        processes=ProcessesConfig(poll_interval=0.02),
        network=NetworkConfig(poll_interval=0.02),
        privacy=PrivacyConfig(capture_environment=True, show_environment=False),
        storage=StorageConfig(database=database, max_events=10_000),
        report=ReportConfig(max_events=100),
        base_dir=tmp_path,
    )
