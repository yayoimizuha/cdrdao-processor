"""Tests for src.isrc.resolver (yt-dlp is mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.isrc.resolver import resolve_titles
from src.models import TrackInfo


def _track(num: int, isrc: str | None) -> TrackInfo:
    return TrackInfo(
        track_number=num,
        isrc=isrc,
        file_offset_frames=0,
        length_frames=1000,
    )


class TestResolveTitle:
    @patch("src.isrc.resolver._resolve_single_isrc")
    def test_skips_no_isrc(self, mock_resolve: MagicMock) -> None:
        tracks = [_track(1, None), _track(2, "")]
        results = resolve_titles(tracks)
        assert len(results) == 2
        assert results[0].title is None
        assert results[1].title is None
        mock_resolve.assert_not_called()

    @patch("src.isrc.resolver._resolve_single_isrc")
    def test_resolves_with_isrc(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = "Some Title"
        tracks = [_track(1, "JPA602400106")]
        results = resolve_titles(tracks)
        assert len(results) == 1
        assert results[0].title == "Some Title"
        assert results[0].isrc == "JPA602400106"
        mock_resolve.assert_called_once_with("JPA602400106")

    @patch("src.isrc.resolver._resolve_single_isrc")
    def test_returns_none_on_empty_result(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = None
        tracks = [_track(1, "JPA602400106")]
        results = resolve_titles(tracks)
        assert results[0].title is None

    @patch("src.isrc.resolver._resolve_single_isrc")
    def test_manually_entered_defaults_false(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = "Title"
        tracks = [_track(1, "JPA000000001")]
        results = resolve_titles(tracks)
        assert results[0].manually_entered is False
