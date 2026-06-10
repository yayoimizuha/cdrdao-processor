"""yt-dlp YouTube 検索による ISRC → 曲名解決。"""

from __future__ import annotations

import time
from typing import Any, Mapping

from src.isrc.cache import IsrcCache
from src.models import ResolvedTitle, TrackInfo

# モジュールレベルで共有するキャッシュインスタンス
_cache = IsrcCache()

# リクエスト間の最小待機秒数
_REQUEST_INTERVAL_SEC: float = 1.0

# exponential backoff の設定
_RETRY_MAX: int = 4          # 最大リトライ回数
_RETRY_BASE_SEC: float = 5.0 # 初回待機秒数（以降 2倍ずつ増加）

# 直前のリクエスト時刻（モジュールレベルで保持）
_last_request_time: float = 0.0


def _extract_title(data: Mapping[str, Any]) -> str | None:
    """yt-dlp の info dict から曲名を抽出する。

    優先順位: ``track`` > ``title`` > ``alt_title`` > ``music.track``
    """
    for key in ("track", "title", "alt_title"):
        val = data.get(key)
        if val and str(val).strip():
            return str(val).strip()

    music = data.get("music")
    if music:
        val = music.get("track") or music.get("title")
        if val and str(val).strip():
            return str(val).strip()

    return None


def _resolve_single_isrc(isrc: str, *, timeout_sec: int = 30) -> str | None:
    """yt-dlp で *isrc* を YouTube 検索し、曲名を返す。

    - キャッシュヒット時はネットワークアクセスをスキップする。
    - 直前のリクエストから ``_REQUEST_INTERVAL_SEC`` 秒未満なら待機する。
    - HTTP 429 など yt-dlp のエラー時は exponential backoff でリトライする。
    """
    global _last_request_time

    # キャッシュ確認
    cached = _cache.get(isrc)
    if cached is not IsrcCache._MISSING:
        return cached  # type: ignore[return-value]

    from yt_dlp import YoutubeDL
    from yt_dlp.utils import ExtractorError

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": timeout_sec,
    }

    for attempt in range(_RETRY_MAX + 1):
        # リクエスト間隔を守る
        elapsed = time.monotonic() - _last_request_time
        wait = _REQUEST_INTERVAL_SEC - elapsed
        if wait > 0:
            time.sleep(wait)

        try:
            with YoutubeDL(opts) as ydl:
                _last_request_time = time.monotonic()
                data = ydl.extract_info(f"ytsearch1:{isrc}", download=False)

            entries = data.get("entries")
            if entries:
                for entry in entries:
                    title = _extract_title(entry)
                    if title:
                        _cache.set(isrc, title)
                        return title
                _cache.set(isrc, None)
                return None

            title = _extract_title(data)
            _cache.set(isrc, title)
            return title

        except ExtractorError as e:
            is_rate_limited = "429" in str(e) or "Too Many Requests" in str(e)
            if attempt >= _RETRY_MAX or not is_rate_limited:
                raise
            backoff = _RETRY_BASE_SEC * (2 ** attempt)
            print(
                f"  [rate limit] ISRC={isrc} リトライ {attempt + 1}/{_RETRY_MAX}"
                f"、{backoff:.0f}秒待機..."
            )
            time.sleep(backoff)
            _last_request_time = time.monotonic()

    return None  # unreachable


def resolve_titles(tracks: list[TrackInfo]) -> list[ResolvedTitle]:
    """全トラックの曲名を解決する。

    - ISRC のないトラックは ``title=None`` で返す。
    - yt-dlp のエラーは例外としてそのまま伝播する。
    """
    results: list[ResolvedTitle] = []
    for t in tracks:
        if not t.isrc:
            results.append(
                ResolvedTitle(track_number=t.track_number, isrc=None, title=None)
            )
            continue

        title = _resolve_single_isrc(t.isrc)
        results.append(
            ResolvedTitle(track_number=t.track_number, isrc=t.isrc, title=title)
        )

    return results
