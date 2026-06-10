"""iTunes Search API によるアルバムアートワーク検索。

Apple の公開 API を利用するため API キー不要。
著作権的にクリーンな高解像度アートワーク URL を取得できる。

インターフェース:
    search_artwork(album: str, artist: str, *, limit: int = 8)
        -> list[ArtworkResult]

ArtworkResult:
    url_100   : 100x100 サムネイル URL（Gradio ギャラリー表示用）
    url_600   : 600x600 高解像度 URL（プレビュー・保存用）
    album     : アルバム名
    artist    : アーティスト名
    country   : ストア国コード (e.g. "JP")
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"

# 検索するストア国（日本を優先、見つからなければ US も試みる）
_COUNTRIES = ["JP", "US"]

# リクエストタイムアウト（秒）
_TIMEOUT = 15.0


@dataclass
class ArtworkResult:
    """iTunes Search API から取得したアルバムアートワーク情報。"""

    url_100: str
    """100x100 サムネイル URL（ギャラリー表示用）"""

    url_hires: str
    """最高解像度・ロスレス URL（5000x5000-999、保存用）"""

    album: str
    """アルバム名"""

    artist: str
    """アーティスト名"""

    country: str
    """ストア国コード"""

    collection_id: int
    """iTunes コレクション ID"""

    @property
    def display_label(self) -> str:
        return f"{self.artist} — {self.album} [{self.country}]"


def _upscale_url(url_100: str) -> str:
    """iTunes アートワーク URL を最高解像度・ロスレス形式に変換する。

    iTunes の URL は末尾が /{元ファイル名}/100x100bb.jpg という構造になっている。
    100x100bb.jpg を {元ファイル名}/5000x5000-999.jpg に置き換えることで、
    - 5000x5000: 実質的に最高解像度（サーバー側でクランプされる）
    - -999: ロスレス品質フラグ
    - 元ファイル名の拡張子を維持: 再圧縮による画質劣化を抑制

    例:
        入力: .../ANTCD-A0000017444.jpg/100x100bb.jpg
        出力: .../ANTCD-A0000017444.jpg/5000x5000-999.jpg
    """
    import re
    # 末尾の /100x100bb.jpg を /{元ファイル名}/5000x5000-999.{拡張子} に置換
    # 元ファイル名は 100x100bb.jpg の直前のパスセグメント
    m = re.search(r'/([^/]+\.\w+)/100x100bb\.(\w+)$', url_100)
    if m:
        original_filename = m.group(1)  # e.g. "ANTCD-A0000017444.jpg"
        ext = original_filename.rsplit('.', 1)[-1]  # 元ファイルの拡張子
        base = url_100[:m.start()]
        return f"{base}/{original_filename}/5000x5000-999.{ext}"
    # フォールバック: パターンが一致しない場合は単純置換
    return url_100.replace("100x100bb", "5000x5000-999")


def _parse_results(items: list[dict[str, Any]], country: str) -> list[ArtworkResult]:
    """iTunes API レスポンスの results 配列を ArtworkResult リストに変換。"""
    seen: set[int] = set()
    results: list[ArtworkResult] = []

    for item in items:
        # アルバム（コレクション）のエントリのみ対象
        if item.get("wrapperType") != "collection":
            continue

        collection_id: int = item.get("collectionId", 0)
        if collection_id in seen:
            continue
        seen.add(collection_id)

        url_100: str = item.get("artworkUrl100", "")
        if not url_100:
            continue

        results.append(
            ArtworkResult(
                url_100=url_100,
                url_hires=_upscale_url(url_100),
                album=item.get("collectionName", ""),
                artist=item.get("artistName", ""),
                country=country,
                collection_id=collection_id,
            )
        )

    return results


# ---------------------------------------------------------------------------
# インメモリキャッシュ
# ---------------------------------------------------------------------------

# キー: (query_normalized, limit) → 結果リスト
_cache: dict[tuple[str, int], list[ArtworkResult]] = {}


def _normalize_query(query: str) -> str:
    """キャッシュキー用にクエリを正規化する（大文字小文字・前後空白を統一）。"""
    return " ".join(query.lower().split())


def _fetch(query: str, limit: int) -> list[ArtworkResult]:
    """実際に iTunes Search API を叩く内部関数（キャッシュなし）。"""
    params_base: dict[str, Any] = {
        "term": query,
        "media": "music",
        "entity": "album",
        "limit": min(limit * 2, 25),
        "lang": "ja_jp",
    }

    seen_ids: set[int] = set()
    merged: list[ArtworkResult] = []

    with httpx.Client(timeout=_TIMEOUT) as client:
        for country in _COUNTRIES:
            if len(merged) >= limit:
                break

            params = {**params_base, "country": country}
            try:
                resp = client.get(_ITUNES_SEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                continue

            for r in _parse_results(data.get("results", []), country):
                if r.collection_id not in seen_ids:
                    seen_ids.add(r.collection_id)
                    merged.append(r)
                if len(merged) >= limit:
                    break

    return merged


def _cached_fetch(query: str, limit: int) -> list[ArtworkResult]:
    """キャッシュ付きで iTunes Search API を検索する。

    同一クエリ・同一 limit の結果はプロセス生存中メモリに保持し、
    2回目以降は API を呼ばずに返す。
    """
    key = (_normalize_query(query), limit)
    if key not in _cache:
        _cache[key] = _fetch(query, limit)
    return _cache[key]


def search_artwork(
    album: str,
    artist: str,
    *,
    limit: int = 8,
) -> list[ArtworkResult]:
    """iTunes Search API でアルバムアートワークを検索する。

    JP ストアで検索し、件数が足りなければ US ストアも検索してマージする。
    同一 collectionId は重複排除する。同一クエリはキャッシュから返す。

    Args:
        album: アルバム名（日本語可）。
        artist: アーティスト名（日本語可）。
        limit: 取得する最大件数（デフォルト: 8）。

    Returns:
        ArtworkResult のリスト。見つからない場合は空リスト。
    """
    query = f"{artist} {album}".strip()
    return _cached_fetch(query, limit)


def search_artwork_by_query(
    query: str,
    *,
    limit: int = 8,
) -> list[ArtworkResult]:
    """フリーワードで iTunes Search API を検索する。

    ユーザーが検索クエリを手動編集した場合などに使用する。
    同一クエリはキャッシュから返す。

    Args:
        query: 検索クエリ文字列。
        limit: 取得する最大件数（デフォルト: 8）。

    Returns:
        ArtworkResult のリスト。
    """
    return _cached_fetch(query.strip(), limit)
