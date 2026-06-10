"""Tests for src.registry.loader."""

import pytest
import pandas as pd
from pathlib import Path

from src.registry.loader import load_registry


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    """Create a minimal registry XLSX for testing."""
    data = {
        "Disc Number": ["TEST-001", "TEST-001", "TEST-002", "TEST-002"],
        "Track Order": [1, 2, 1, 2],
        "Track Title": ["Song A", "Song B", "Song X", "Song Y"],
        "Release Title": ["Album 1", "Album 1", "Album 2", "Album 2"],
        "Artist": ["Artist A", "Artist A", "Artist B", "Artist B"],
        "Release Date": ["2024-01-01", "2024-01-01", "2024-06-15", "2024-06-15"],
        "Label": [None, None, "Label B", "Label B"],
        "Release Type": ["single", "single", "album", "album"],
        "Disc Type": [None, None, None, None],
        "Lyricist": ["L1", None, "L2", "L3"],
        "Composer": ["C1", "C2", None, None],
        "Arranger": [None, None, None, None],
        "Singer": [None, None, "S1", "S2"],
    }
    df = pd.DataFrame(data)
    path = tmp_path / "registry.xlsx"
    df.to_excel(path, index=False, engine="openpyxl")
    return path


@pytest.fixture
def missing_col_xlsx(tmp_path: Path) -> Path:
    """Create an XLSX missing required columns."""
    data = {"Disc Number": ["X-001"], "Track Order": [1]}
    df = pd.DataFrame(data)
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False, engine="openpyxl")
    return path


class TestLoadRegistry:
    def test_disc_count(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        assert len(records) == 2

    def test_disc_numbers(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        numbers = {r.disc_number for r in records}
        assert numbers == {"TEST-001", "TEST-002"}

    def test_track_count(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        for r in records:
            assert len(r.tracks) == 2

    def test_disc_metadata(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        d1 = next(r for r in records if r.disc_number == "TEST-001")
        assert d1.release_title == "Album 1"
        assert d1.artist == "Artist A"
        assert d1.release_type == "single"

    def test_track_metadata(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        d1 = next(r for r in records if r.disc_number == "TEST-001")
        assert d1.tracks[0].title == "Song A"
        assert d1.tracks[0].lyricist == "L1"
        assert d1.tracks[0].composer == "C1"

    def test_none_values(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        d1 = next(r for r in records if r.disc_number == "TEST-001")
        assert d1.label is None
        assert d1.tracks[1].lyricist is None

    def test_track_ordering(self, sample_xlsx: Path) -> None:
        records = load_registry(sample_xlsx)
        for r in records:
            orders = [t.order for t in r.tracks]
            assert orders == sorted(orders)

    def test_missing_columns(self, missing_col_xlsx: Path) -> None:
        with pytest.raises(ValueError, match="missing required columns"):
            load_registry(missing_col_xlsx)
