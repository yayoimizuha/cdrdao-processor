"""``merged_release_registry.xlsx`` を :class:`DiscRecord` に読み込む。"""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

from src.models import DiscRecord, TrackRecord

# ---------------------------------------------------------------------------
# XLSX 列名定義
# ---------------------------------------------------------------------------

_COL_DISC = "Disc Number"
_COL_ORDER = "Track Order"
_COL_TITLE = "Track Title"

_REQUIRED_COLS = {_COL_DISC, _COL_ORDER, _COL_TITLE}

_COL_MAP_DISC = {
    "Release Title": "release_title",
    "Artist": "artist",
    "Release Date": "release_date",
    "Label": "label",
    "Release Type": "release_type",
    "Disc Type": "disc_type",
}

_COL_MAP_TRACK = {
    "Lyricist": "lyricist",
    "Composer": "composer",
    "Arranger": "arranger",
    "Singer": "singer",
}


def _str_or_none(val: object) -> str | None:
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def load_registry(xlsx_path: Path) -> list[DiscRecord]:
    """*xlsx_path* を読み込み、品番ごとに :class:`DiscRecord` を返す。

    必須列が欠落している場合は :class:`ValueError` を送出する。
    """
    df = pd.read_excel(xlsx_path, engine="openpyxl")

    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"Registry is missing required columns: {', '.join(sorted(missing))}"
        )

    # 型の正規化
    df[_COL_DISC] = df[_COL_DISC].astype(str).str.strip()
    df[_COL_ORDER] = pd.to_numeric(df[_COL_ORDER], errors="coerce")
    df = df.dropna(subset=[_COL_ORDER])
    df[_COL_ORDER] = df[_COL_ORDER].astype(int)
    df = df.sort_values(by=[_COL_DISC, _COL_ORDER])

    # 重複行の除去 (Disc Number + Track Order) — 先行行を優先
    dup_mask = df.duplicated(subset=[_COL_DISC, _COL_ORDER], keep="first")
    if dup_mask.any():
        for _, row in df[dup_mask].iterrows():
            warnings.warn(
                f"Duplicate row dropped: {row[_COL_DISC]} track {row[_COL_ORDER]}",
                stacklevel=2,
            )
    df = df[~dup_mask]

    records: list[DiscRecord] = []
    for disc_num, group in df.groupby(_COL_DISC, sort=False):
        first = group.iloc[0]

        disc_kwargs: dict[str, str | None] = {}
        for xlsx_col, attr in _COL_MAP_DISC.items():
            disc_kwargs[attr] = _str_or_none(first.get(xlsx_col))

        track_list: list[TrackRecord] = []
        for _, row in group.iterrows():
            track_kwargs: dict[str, str | None] = {}
            for xlsx_col, attr in _COL_MAP_TRACK.items():
                track_kwargs[attr] = _str_or_none(row.get(xlsx_col))

            track_list.append(
                TrackRecord(
                    order=int(row[_COL_ORDER]),
                    title=str(row[_COL_TITLE]).strip(),
                    **track_kwargs,
                )
            )

        records.append(
            DiscRecord(
                disc_number=str(disc_num),
                tracks=track_list,
                **disc_kwargs,
            )
        )

    return records
