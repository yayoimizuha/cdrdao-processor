"""Tests for src.matching.scorer."""

import pytest

from src.matching.scorer import (
    ScorerConfig,
    normalize_title,
    score_candidates,
    should_auto_pick,
)
from src.models import DiscCandidate, DiscRecord, ResolvedTitle, TrackRecord


def _disc(disc_number: str, titles: list[str]) -> DiscRecord:
    return DiscRecord(
        disc_number=disc_number,
        tracks=[TrackRecord(order=i + 1, title=t) for i, t in enumerate(titles)],
    )


def _resolved(titles: list[str | None]) -> list[ResolvedTitle]:
    return [
        ResolvedTitle(track_number=i + 1, isrc=None, title=t)
        for i, t in enumerate(titles)
    ]


class TestNormalizeTitle:
    def test_basic(self) -> None:
        assert normalize_title("Hello World!") == "helloworld"

    def test_japanese(self) -> None:
        assert normalize_title("ハロー！ワールド") == "ハローワールド"

    def test_fancy_quotes(self) -> None:
        assert normalize_title("\u201cHello\u201d") == "hello"

    def test_wave_dash(self) -> None:
        assert normalize_title("A\u301cB") == "ab"
        assert normalize_title("A\uff5eB") == "ab"

    def test_none(self) -> None:
        assert normalize_title(None) == ""

    def test_empty(self) -> None:
        assert normalize_title("") == ""


class TestScoring:
    def test_perfect_match(self) -> None:
        resolved = _resolved(["Song A", "Song B", "Song C"])
        registry = [_disc("X-001", ["Song A", "Song B", "Song C"])]
        cands = score_candidates(resolved, registry)
        assert len(cands) == 1
        assert cands[0].score == pytest.approx(1.0)

    def test_no_match(self) -> None:
        resolved = _resolved(["Song A", "Song B"])
        registry = [_disc("X-001", ["Totally Different", "Other Song"])]
        cands = score_candidates(resolved, registry)
        # Score should be 0 (no overlap), so no candidates pass the filter
        assert len(cands) == 0

    def test_partial_containment(self) -> None:
        resolved = _resolved(["Song A"])
        registry = [_disc("X-001", ["Song A (Special Ver.)"])]
        cands = score_candidates(resolved, registry)
        assert len(cands) == 1
        # Containment gives 0.5
        assert cands[0].score == pytest.approx(0.5)

    def test_none_titles_excluded(self) -> None:
        resolved = _resolved([None, "Song B"])
        registry = [_disc("X-001", ["Whatever", "Song B"])]
        cands = score_candidates(resolved, registry)
        assert len(cands) == 1
        assert cands[0].score > 0

    def test_length_penalty(self) -> None:
        resolved = _resolved(["Song A", "Song B"])
        disc_3 = _disc("X-001", ["Song A", "Song B", "Song C"])
        disc_2 = _disc("X-002", ["Song A", "Song B"])
        cands = score_candidates(resolved, [disc_3, disc_2])
        # disc_2 should score higher (no length penalty)
        scores = {c.disc_number: c.score for c in cands}
        assert scores["X-002"] > scores["X-001"]

    def test_ordering_descending(self) -> None:
        resolved = _resolved(["Song A", "Song B"])
        registry = [
            _disc("X-001", ["Song A", "Song B"]),
            _disc("X-002", ["Song A", "Wrong"]),
        ]
        cands = score_candidates(resolved, registry)
        assert len(cands) >= 1
        for i in range(len(cands) - 1):
            assert cands[i].score >= cands[i + 1].score


class TestAutoPick:
    def test_auto_pick_clear_winner(self) -> None:
        cands = [
            DiscCandidate("X-001", None, None, None, score=0.90),
            DiscCandidate("X-002", None, None, None, score=0.30),
        ]
        assert should_auto_pick(cands) is True

    def test_no_auto_pick_close_scores(self) -> None:
        cands = [
            DiscCandidate("X-001", None, None, None, score=0.80),
            DiscCandidate("X-002", None, None, None, score=0.75),
        ]
        assert should_auto_pick(cands) is False

    def test_no_auto_pick_low_score(self) -> None:
        cands = [
            DiscCandidate("X-001", None, None, None, score=0.50),
        ]
        assert should_auto_pick(cands) is False

    def test_empty_candidates(self) -> None:
        assert should_auto_pick([]) is False
