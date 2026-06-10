"""OutputFormatter プロトコルと共通ペイロードビルダー。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.models import (
    DiscRecord,
    ResolvedTitle,
    TocResult,
    frames_to_msf,
)


@runtime_checkable
class OutputFormatter(Protocol):
    """メタデータ出力フォーマッターの構造的部分型。"""

    format_name: str
    file_extension: str  # ドット含む (例: ".json")

    def write(self, payload: dict[str, Any], dest: Path) -> None:
        """*payload* を *dest* にシリアライズする。"""
        ...


def build_payload(
    toc: TocResult,
    disc: DiscRecord,
    resolved: list[ResolvedTitle],
) -> dict[str, Any]:
    """全フォーマッター共通の出力辞書を構築する。"""

    resolved_map = {r.track_number: r for r in resolved}

    tracks_out: list[dict[str, Any]] = []
    for tr in disc.tracks:
        ti = next(
            (t for t in toc.tracks if t.track_number == tr.order),
            None,
        )
        rt = resolved_map.get(tr.order)

        bin_offset: dict[str, Any] | None = None
        if ti is not None:
            bin_offset = {
                "file_offset_frames": ti.file_offset_frames,
                "audio_offset_frames": ti.audio_offset_frames,
                "audio_length_frames": ti.audio_length_frames,
                "audio_offset_msf": frames_to_msf(ti.audio_offset_frames),
                "audio_length_msf": frames_to_msf(ti.audio_length_frames),
            }

        tracks_out.append(
            {
                "track_number": tr.order,
                "isrc": rt.isrc if rt else None,
                "title": tr.title,
                "lyricist": tr.lyricist,
                "composer": tr.composer,
                "arranger": tr.arranger,
                "singer": tr.singer,
                "manually_entered": rt.manually_entered if rt else False,
                "bin_offset": bin_offset,
            }
        )

    return {
        "source": {
            "toc_path": str(toc.toc_path.resolve()),
            "bin_path": str(toc.bin_path.resolve()),
            "catalog": toc.catalog,
        },
        "disc": {
            "disc_number": disc.disc_number,
            "release_title": disc.release_title,
            "artist": disc.artist,
            "release_date": disc.release_date,
            "label": disc.label,
            "release_type": disc.release_type,
            "disc_type": disc.disc_type,
        },
        "tracks": tracks_out,
    }
