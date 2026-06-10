"""cdrdao CD-DA イメージの TOC ファイルパーサ。

cdrdao が生成した ``.toc`` ファイルをパースし :class:`TocResult` を返す。
``CD_DA`` (Audio CD) フォーマットのみ対応。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.models import TrackInfo, TocResult, msf_to_frames

# ---------------------------------------------------------------------------
# 正規表現パターン
# ---------------------------------------------------------------------------

_RE_CATALOG = re.compile(r'^CATALOG\s+"([^"]+)"')
_RE_ISRC = re.compile(r'^ISRC\s+"([^"]+)"')
_RE_FILE = re.compile(r'^FILE\s+"([^"]+)"\s+(\S+)\s+(\S+)')
_RE_START = re.compile(r"^START\s+(\S+)")

# 認識するが処理不要な行
_IGNORE_TOKENS = frozenset(
    {
        "CD_DA",
        "NO COPY",
        "COPY",
        "NO PRE_EMPHASIS",
        "PRE_EMPHASIS",
        "TWO_CHANNEL_AUDIO",
        "FOUR_CHANNEL_AUDIO",
    }
)


def _iter_lines(content: str):
    """空行・コメント行を除いた有効行を返す。"""
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        yield line


def parse_toc_string(content: str) -> tuple[str | None, list[TrackInfo]]:
    """TOC 文字列をパースし ``(catalog, tracks)`` を返す。

    ``CD_DA`` ヘッダがない場合は :class:`ValueError` を送出する。
    """
    catalog: str | None = None
    tracks: list[TrackInfo] = []

    track_number = 0
    cur_isrc: str | None = None
    cur_file: tuple[int, int] | None = None  # (offset_frames, length_frames)
    cur_start: int | None = None

    seen_cd_da = False

    def _flush() -> None:
        nonlocal cur_isrc, cur_file, cur_start
        if cur_file is not None:
            tracks.append(
                TrackInfo(
                    track_number=track_number,
                    isrc=cur_isrc,
                    file_offset_frames=cur_file[0],
                    length_frames=cur_file[1],
                    pregap_frames=cur_start,
                )
            )
        cur_isrc = None
        cur_file = None
        cur_start = None

    for line in _iter_lines(content):
        if line in _IGNORE_TOKENS:
            if line == "CD_DA":
                seen_cd_da = True
            continue

        if m := _RE_CATALOG.match(line):
            catalog = m.group(1)
            continue

        if line == "TRACK AUDIO":
            _flush()
            track_number += 1
            continue

        if m := _RE_ISRC.match(line):
            cur_isrc = m.group(1)
            continue

        if m := _RE_FILE.match(line):
            cur_file = (
                msf_to_frames(m.group(2)),
                msf_to_frames(m.group(3)),
            )
            continue

        if m := _RE_START.match(line):
            cur_start = msf_to_frames(m.group(1))
            continue

        # 未知の行は無視する

    _flush()

    if not seen_cd_da:
        raise ValueError("TOC does not contain CD_DA header — only CD-DA is supported")

    return catalog, tracks


def parse_toc(toc_path: Path, *, encoding: str = "utf-8") -> TocResult:
    """``.toc`` ファイルをパースし :class:`TocResult` を返す。

    ``bin_path`` は同ディレクトリの ``<toc_stem>.bin`` に自動解決される。
    """
    content = toc_path.read_text(encoding=encoding)
    catalog, tracks = parse_toc_string(content)
    bin_path = toc_path.with_suffix(".bin")
    return TocResult(
        toc_path=toc_path,
        bin_path=bin_path,
        catalog=catalog,
        tracks=tracks,
    )
