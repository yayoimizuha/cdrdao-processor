"""Tests for src.toc.parser."""

import pytest

from src.models import TocResult, TrackInfo, msf_to_frames, frames_to_msf, FRAMES_PER_SECOND
from src.toc.parser import parse_toc_string


# ── Sample TOC content (matches tocs/EPCE-7845.toc) ──

SAMPLE_TOC = """\
CD_DA


// Track 1
TRACK AUDIO
NO COPY
NO PRE_EMPHASIS
TWO_CHANNEL_AUDIO
ISRC "JPA602400106"
FILE "EPCE-7845.bin" 0 04:27:34

// Track 2
TRACK AUDIO
NO COPY
NO PRE_EMPHASIS
TWO_CHANNEL_AUDIO
ISRC "JPA602400107"
FILE "EPCE-7845.bin" 04:27:34 04:43:33
START 00:00:12

// Track 3
TRACK AUDIO
NO COPY
NO PRE_EMPHASIS
TWO_CHANNEL_AUDIO
ISRC "JPA602400107"
FILE "EPCE-7845.bin" 09:10:67 04:23:48
START 00:00:12
"""


class TestMsfConversion:
    def test_msf_to_frames_zero(self) -> None:
        assert msf_to_frames("0") == 0
        assert msf_to_frames("00:00:00") == 0

    def test_msf_roundtrip(self) -> None:
        assert frames_to_msf(msf_to_frames("04:27:34")) == "04:27:34"
        assert frames_to_msf(msf_to_frames("00:00:12")) == "00:00:12"

    def test_msf_to_frames_calculation(self) -> None:
        # 1:00:00 = 60 * 75 = 4500 frames
        assert msf_to_frames("01:00:00") == 4500
        # 0:01:00 = 75 frames
        assert msf_to_frames("00:01:00") == 75

    def test_invalid_msf(self) -> None:
        with pytest.raises(ValueError):
            msf_to_frames("invalid")

    def test_negative_frames(self) -> None:
        with pytest.raises(ValueError):
            frames_to_msf(-1)


class TestTocParser:
    def test_parse_track_count(self) -> None:
        catalog, tracks = parse_toc_string(SAMPLE_TOC)
        assert len(tracks) == 3

    def test_no_catalog(self) -> None:
        catalog, _ = parse_toc_string(SAMPLE_TOC)
        assert catalog is None

    def test_catalog_present(self) -> None:
        content = 'CD_DA\nCATALOG "4988010048747"\nTRACK AUDIO\nFILE "x.bin" 0 01:00:00\n'
        catalog, tracks = parse_toc_string(content)
        assert catalog == "4988010048747"
        assert len(tracks) == 1

    def test_track_numbers(self) -> None:
        _, tracks = parse_toc_string(SAMPLE_TOC)
        assert [t.track_number for t in tracks] == [1, 2, 3]

    def test_isrc_parsing(self) -> None:
        _, tracks = parse_toc_string(SAMPLE_TOC)
        assert tracks[0].isrc == "JPA602400106"
        assert tracks[1].isrc == "JPA602400107"

    def test_offsets_and_lengths(self) -> None:
        _, tracks = parse_toc_string(SAMPLE_TOC)
        assert tracks[0].file_offset_frames == 0
        assert tracks[0].length_frames == msf_to_frames("04:27:34")

    def test_pregap(self) -> None:
        _, tracks = parse_toc_string(SAMPLE_TOC)
        assert tracks[0].pregap_frames is None
        assert tracks[1].pregap_frames == msf_to_frames("00:00:12")
        assert tracks[1].pregap_frames == 12

    def test_audio_offset_no_pregap(self) -> None:
        _, tracks = parse_toc_string(SAMPLE_TOC)
        t = tracks[0]
        assert t.audio_offset_frames == t.file_offset_frames
        assert t.audio_length_frames == t.length_frames

    def test_audio_offset_with_pregap(self) -> None:
        _, tracks = parse_toc_string(SAMPLE_TOC)
        t = tracks[1]
        assert t.audio_offset_frames == t.file_offset_frames + 12
        assert t.audio_length_frames == t.length_frames - 12

    def test_missing_cd_da_header(self) -> None:
        with pytest.raises(ValueError, match="CD_DA"):
            parse_toc_string('TRACK AUDIO\nFILE "x.bin" 0 01:00:00\n')

    def test_empty_toc(self) -> None:
        _, tracks = parse_toc_string("CD_DA\n")
        assert tracks == []
