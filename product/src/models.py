"""cdrdao-processor パイプラインの共有データクラス。

このモジュールは他の src/ モジュールに依存しない。
モジュール間のデータ受け渡しはすべてここで定義された型を通じて行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# CD-DA 定数
# ---------------------------------------------------------------------------

FRAMES_PER_SECOND: int = 75
BYTES_PER_FRAME: int = 2352  # 44100 Hz * 16 bit * 2 ch / 75 fps


def frames_to_msf(total_frames: int) -> str:
    """フレーム数を ``MM:SS:FF`` 文字列に変換する。"""
    if total_frames < 0:
        raise ValueError("total_frames must be non-negative")
    total_seconds, ff = divmod(total_frames, FRAMES_PER_SECOND)
    mm, ss = divmod(total_seconds, 60)
    return f"{mm:02d}:{ss:02d}:{ff:02d}"


def msf_to_frames(msf: str) -> int:
    """``MM:SS:FF`` (または ``"0"``) をフレーム数に変換する。"""
    s = msf.strip()
    if s == "0":
        return 0
    parts = s.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid MSF format: {msf!r}")
    mm, ss, ff = int(parts[0]), int(parts[1]), int(parts[2])
    return (mm * 60 + ss) * FRAMES_PER_SECOND + ff


# ---------------------------------------------------------------------------
# TOC パース結果
# ---------------------------------------------------------------------------


@dataclass
class TrackInfo:
    """BIN ファイル内の 1 トラック分の物理情報。"""

    track_number: int
    isrc: str | None
    file_offset_frames: int
    length_frames: int
    pregap_frames: int | None = None

    @property
    def audio_offset_frames(self) -> int:
        """実音声の開始フレーム (プリギャップ除く)。"""
        if self.pregap_frames is None:
            return self.file_offset_frames
        return self.file_offset_frames + self.pregap_frames

    @property
    def audio_length_frames(self) -> int:
        """実音声の長さ (プリギャップ除く)。"""
        if self.pregap_frames is None:
            return self.length_frames
        return self.length_frames - self.pregap_frames


@dataclass
class TocResult:
    """``.toc`` ファイル 1 枚分のパース結果。"""

    toc_path: Path
    bin_path: Path
    catalog: str | None
    tracks: list[TrackInfo]


# ---------------------------------------------------------------------------
# ISRC 曲名解決結果
# ---------------------------------------------------------------------------


@dataclass
class ResolvedTitle:
    """1 トラックの曲名解決結果。"""

    track_number: int
    isrc: str | None
    title: str | None
    manually_entered: bool = False


# ---------------------------------------------------------------------------
# レジストリ (XLSX) レコード
# ---------------------------------------------------------------------------


@dataclass
class TrackRecord:
    """リリースレジストリ上の 1 トラック。"""

    order: int
    title: str
    lyricist: str | None = None
    composer: str | None = None
    arranger: str | None = None
    singer: str | None = None


@dataclass
class DiscRecord:
    """リリースレジストリ上のディスク (品番単位)。"""

    disc_number: str
    release_title: str | None = None
    artist: str | None = None
    release_date: str | None = None
    label: str | None = None
    release_type: str | None = None
    disc_type: str | None = None
    tracks: list[TrackRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# マッチング結果
# ---------------------------------------------------------------------------


@dataclass
class DiscCandidate:
    """スコアリングで算出した候補ディスク。"""

    disc_number: str
    release_title: str | None
    artist: str | None
    release_date: str | None
    score: float
