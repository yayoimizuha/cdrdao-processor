"""Tests for output formatters (JSON / YAML)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.models import (
    DiscRecord,
    ResolvedTitle,
    TocResult,
    TrackInfo,
    TrackRecord,
)
from src.output.base import build_payload
from src.output.json_formatter import JsonFormatter
from src.output.yaml_formatter import YamlFormatter


def _make_payload() -> dict:
    toc = TocResult(
        toc_path=Path("/tmp/TEST.toc"),
        bin_path=Path("/tmp/TEST.bin"),
        catalog="4988010048747",
        tracks=[
            TrackInfo(track_number=1, isrc="JPA000000001", file_offset_frames=0, length_frames=20000),
            TrackInfo(track_number=2, isrc=None, file_offset_frames=20000, length_frames=15000, pregap_frames=12),
        ],
    )
    disc = DiscRecord(
        disc_number="TEST-001",
        release_title="Test Album",
        artist="Test Artist",
        release_date="2024-01-01",
        tracks=[
            TrackRecord(order=1, title="Song A", lyricist="L", composer="C"),
            TrackRecord(order=2, title="Song B"),
        ],
    )
    resolved = [
        ResolvedTitle(track_number=1, isrc="JPA000000001", title="Song A"),
        ResolvedTitle(track_number=2, isrc=None, title="Song B", manually_entered=True),
    ]
    return build_payload(toc, disc, resolved)


class TestBuildPayload:
    def test_source_section(self) -> None:
        p = _make_payload()
        assert p["source"]["catalog"] == "4988010048747"

    def test_disc_section(self) -> None:
        p = _make_payload()
        assert p["disc"]["disc_number"] == "TEST-001"
        assert p["disc"]["artist"] == "Test Artist"

    def test_tracks_count(self) -> None:
        p = _make_payload()
        assert len(p["tracks"]) == 2

    def test_track_bin_offset(self) -> None:
        p = _make_payload()
        t1 = p["tracks"][0]
        assert t1["bin_offset"]["file_offset_frames"] == 0
        assert t1["bin_offset"]["audio_offset_frames"] == 0

    def test_track_pregap_offset(self) -> None:
        p = _make_payload()
        t2 = p["tracks"][1]
        assert t2["bin_offset"]["audio_offset_frames"] == 20000 + 12
        assert t2["bin_offset"]["audio_length_frames"] == 15000 - 12

    def test_manually_entered(self) -> None:
        p = _make_payload()
        assert p["tracks"][0]["manually_entered"] is False
        assert p["tracks"][1]["manually_entered"] is True


class TestJsonFormatter:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        payload = _make_payload()
        dest = tmp_path / "test.json"
        JsonFormatter().write(payload, dest)
        assert dest.exists()
        data = json.loads(dest.read_text(encoding="utf-8"))
        assert data["disc"]["disc_number"] == "TEST-001"


class TestYamlFormatter:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        payload = _make_payload()
        dest = tmp_path / "test.yaml"
        YamlFormatter().write(payload, dest)
        assert dest.exists()
        data = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert data["disc"]["disc_number"] == "TEST-001"
