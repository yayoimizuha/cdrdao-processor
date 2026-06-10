"""ISRC → 曲名解決結果の永続キャッシュ。

キャッシュファイルは JSON 形式で保存され、プロセス再起動後も維持される。
デフォルト保存先: ``<product>/cache/isrc_cache.json``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

# デフォルトのキャッシュファイルパス（product/ 直下の cache/ ディレクトリ）
_DEFAULT_CACHE_PATH = Path(__file__).parents[2] / "cache" / "isrc_cache.json"


class IsrcCache:
    """ISRC をキーとした曲名の永続キャッシュ。

    - ヒット時: ``get(isrc)`` が ``str | None`` を返す（``None`` は「検索済みだが未解決」）
    - ミス時: ``get(isrc)`` が ``_MISSING`` センチネルを返す
    - 書き込み: ``set(isrc, title)`` で追加し、即座にファイルへ保存する
    """

    _MISSING = object()

    def __init__(self, path: Path = _DEFAULT_CACHE_PATH) -> None:
        self._path = path
        self._data: dict[str, str | None] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, isrc: str) -> str | None | object:
        """キャッシュを引く。

        Returns:
            キャッシュヒット時: ``str``（曲名）または ``None``（解決済み・未取得）
            キャッシュミス時: ``IsrcCache._MISSING``
        """
        if isrc in self._data:
            return self._data[isrc]
        return self._MISSING

    def set(self, isrc: str, title: str | None) -> None:
        """結果をキャッシュに保存し、即座にファイルへ書き出す。"""
        self._data[isrc] = title
        self._save()

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    @property
    def path(self) -> Path:
        return self._path
