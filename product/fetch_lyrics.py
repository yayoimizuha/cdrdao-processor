"""fetch_lyrics.py — uta-net.com から歌詞を取得して .m4a の ©lyr タグに書き込む。

使い方:
    uv run python fetch_lyrics.py <.m4aファイル>
    uv run python fetch_lyrics.py <ディレクトリ>

処理フロー (1ファイルごと):
    1. mutagen で ©nam (タイトル) / ©ART (アーティスト) を読み取る
    2. uta-net.com で曲名検索 → 候補一覧を取得
    3. 曲名+アーティストで自動マッチ。複数候補が残ればインタラクティブに選択
    4. 歌詞ページから歌詞テキストを取得
    5. ©lyr タグに書き込む（既存は上書き）
    6. 1 秒待機して次のファイルへ

uta-net のフェッチは fetch.py と同じくサブプロセス + Playwright で行う。
（Cloudflare 等を回避するためブラウザを使う必要がある）
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from mutagen.mp4 import MP4

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent

_SONG_URL    = "https://www.uta-net.com/song/{song_id}/"
_SONG_URL_RE = re.compile(r"https://www\.uta-net\.com/song/(\d+)")

_SUBPROCESS_TIMEOUT = 45  # 秒

# 自動選択の閾値: 最高スコアが2位をこの値以上上回れば自動選択する
_AUTO_SELECT_MARGIN = 0.15


# ---------------------------------------------------------------------------
# Playwright サブプロセスで HTML を取得する低レベル関数
# ---------------------------------------------------------------------------

def _fetch_html_subprocess(url: str) -> str:
    """サブプロセス経由で Playwright を起動し、ページの HTML を返す。

    fetch.py の設計に倣い、Playwright を GIL の外に追い出す。
    戻り値は HTML 文字列。失敗時は RuntimeError を送出する。
    """
    result = subprocess.run(
        [sys.executable, "-m", "fetch_lyrics", "--_fetch", url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=_PROJECT_ROOT,
    )
    if not result.stdout.strip():
        raise RuntimeError(
            f"fetch subprocess returned no output (exit {result.returncode}): "
            f"{result.stderr.strip()[:200]}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"fetch subprocess returned invalid JSON: {result.stdout[:200]}"
        ) from exc
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "fetch: unknown error"))
    return payload["html"]


def _run_fetch_subprocess(url: str) -> None:
    """__main__ の --_fetch モードで呼ばれる実際のブラウザ処理。

    結果を JSON {"ok": true, "html": "..."} として stdout に書き出す。
    """
    from fake_useragent import UserAgent
    from playwright.sync_api import sync_playwright

    user_agent = UserAgent(
        browsers=["Chrome", "Edge"],
        os=["Windows", "Mac OS X"],
        platforms=["desktop"],
        min_version=120.0,
    ).random

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx = browser.new_context(
                    user_agent=user_agent,
                    locale="ja-JP",
                    viewport={"width": 1280, "height": 900},
                )
                page = ctx.new_page()
                page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in {"image", "media", "font"}
                        else route.continue_()
                    ),
                )
                page.goto(url, timeout=20_000, wait_until="load")
                page.wait_for_timeout(1_000)
                html = page.content()
            finally:
                browser.close()
        print(json.dumps({"ok": True, "html": html}))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Yahoo 検索 → uta-net song URL リスト
# ---------------------------------------------------------------------------

# Yahoo が Bot 判定で空返しすることがあるためリトライ回数を設定する
_SEARCH_RETRIES = 3
_SEARCH_RETRY_WAIT = 2.0  # 秒


def search_song_url(title: str, artist: str) -> list[str]:
    """Yahoo 検索で "uta-net 歌詞 {title} {artist}" を検索し、
    https://www.uta-net.com/song/XXXXX/ 形式の URL リストを返す（重複排除済み）。

    src.enrich.search.search() を使うことで search.py の Playwright 設定
    （UA ローテート・セレクタ待機等）をそのまま利用する。
    Yahoo の Bot 判定で 0 件になった場合は _SEARCH_RETRIES 回までリトライする。
    """
    from src.enrich.search import search

    query = f"uta-net 歌詞 {title} {artist}"

    for attempt in range(1, _SEARCH_RETRIES + 1):
        results = search(query)
        urls: list[str] = []
        seen: set[str] = set()
        for snippet in results:
            for m in _SONG_URL_RE.finditer(snippet):
                url = f"https://www.uta-net.com/song/{m.group(1)}/"
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        if urls:
            return urls
        if attempt < _SEARCH_RETRIES:
            time.sleep(_SEARCH_RETRY_WAIT)

    return []


def _fetch_song_page_title(url: str) -> str:
    """uta-net 曲ページの <title> テキストを返す。取得失敗時は URL をそのまま返す。"""
    from bs4 import BeautifulSoup

    try:
        html = _fetch_html_subprocess(url)
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.find("title")
        return title_tag.get_text(strip=True) if title_tag else url
    except Exception:  # noqa: BLE001
        return url


def fetch_lyrics_text(song_id: str) -> str | None:
    """uta-net の曲ページから歌詞テキストを取得する。

    <br> / <br/> を改行に変換する。
    歌詞が見つからなければ None を返す。
    """
    from bs4 import BeautifulSoup

    url = _SONG_URL.format(song_id=song_id)
    html = _fetch_html_subprocess(url)
    soup = BeautifulSoup(html, "lxml")

    kashi_div = soup.find(id="kashi_area")
    if kashi_div is None:
        return None

    # <br> を改行に変換してテキスト抽出
    for br in kashi_div.find_all("br"):
        br.replace_with("\n")

    return kashi_div.get_text()


# ---------------------------------------------------------------------------
# 自動選択 / インタラクティブ選択
# ---------------------------------------------------------------------------

def _try_auto_select(
    entries: list[tuple[str, str]],
    title: str,
    artist: str,
) -> tuple[str, str] | None:
    """ページタイトルの類似度で自動選択を試みる。

    期待タイトル "{artist} {title} 歌詞 - 歌ネット" と各ページタイトルの
    SequenceMatcher ratio を計算し、最高スコアが2位より _AUTO_SELECT_MARGIN
    以上高ければそのエントリーを返す。それ以外は None を返す。

    Args:
        entries: [(url, page_title), ...] のリスト。
        title: 曲タイトル（©nam）。
        artist: アーティスト名（©ART）。

    Returns:
        自動選択された (url, page_title)、または None。
    """
    expected = f"{artist} {title} 歌詞 - 歌ネット"

    def _ratio(page_title: str) -> float:
        return difflib.SequenceMatcher(None, expected, page_title).ratio()

    scored = sorted(entries, key=lambda e: _ratio(e[1]), reverse=True)
    best_score  = _ratio(scored[0][1])
    second_score = _ratio(scored[1][1]) if len(scored) > 1 else 0.0

    if best_score - second_score >= _AUTO_SELECT_MARGIN:
        return scored[0]
    return None


def _interactive_select(
    entries: list[tuple[str, str]],
    title: str,
    artist: str,
) -> str | None:
    """複数の song URL をページタイトルと共に表示してユーザーに選択させる。

    Args:
        entries: [(url, page_title), ...] のリスト（取得済み）。
        title: 曲タイトル（表示用）。
        artist: アーティスト名（表示用）。

    0 を選べばスキップ。
    """
    print(f"\n  [{title} / {artist}] — 複数の候補が見つかりました:")
    for i, (url, page_title) in enumerate(entries, start=1):
        print(f"    {i}: {page_title}")
        print(f"       {url}")
    print("    0: スキップ")
    while True:
        try:
            raw = input("  番号を選択 > ").strip()
            n = int(raw)
        except (ValueError, EOFError):
            print("  数字を入力してください。")
            continue
        if n == 0:
            return None
        if 1 <= n <= len(entries):
            return entries[n - 1][0]
        print(f"  1〜{len(entries)} または 0 を入力してください。")


# ---------------------------------------------------------------------------
# 1 ファイル処理
# ---------------------------------------------------------------------------

def process_m4a(path: Path) -> None:
    """1 つの .m4a ファイルに対して歌詞取得・タグ書き込みを行う。"""
    f = MP4(str(path))
    if f.tags is None:
        print(f"  スキップ (タグなし): {path.name}")
        return

    title_tag  = f.tags.get("©nam")
    artist_tag = f.tags.get("©ART")
    title  = title_tag[0]  if title_tag  else ""
    artist = artist_tag[0] if artist_tag else ""

    if not title:
        print(f"  スキップ (タイトルなし): {path.name}")
        return

    # 既存歌詞があればスキップ
    if f.tags.get("©lyr"):
        print(f"  スキップ (歌詞取得済み): {path.name}")
        return

    print(f"  検索中: {title!r} / {artist!r}", end="", flush=True)

    try:
        urls = search_song_url(title, artist)
    except Exception as exc:
        print(f"\n  エラー (検索失敗): {exc}")
        return

    if not urls:
        print(f"\n  見つかりません: {path.name}")
        return

    if len(urls) == 1:
        chosen_url = urls[0]
        print(f" → {chosen_url}")
    else:
        # 全URLのページタイトルを取得して自動選択を試みる
        print(f" ({len(urls)}件ヒット、ページタイトル取得中...)")
        entries = [(url, _fetch_song_page_title(url)) for url in urls]

        auto = _try_auto_select(entries, title, artist)
        if auto:
            chosen_url, auto_title = auto
            print(f"  自動選択: {auto_title}")
            print(f"           {chosen_url}")
        else:
            chosen_url = _interactive_select(entries, title, artist)
            if chosen_url is None:
                print(f"  スキップ: {path.name}")
                return

    m = _SONG_URL_RE.search(chosen_url)
    if not m:
        print(f"  エラー (song_id を抽出できません): {chosen_url}")
        return
    song_id = m.group(1)

    print(f"  歌詞取得中 (song_id={song_id})", end="", flush=True)
    try:
        lyrics = fetch_lyrics_text(song_id)
    except Exception as exc:
        print(f"\n  エラー (歌詞取得失敗): {exc}")
        return

    if lyrics is None:
        print(f"\n  歌詞エリアが見つかりません: {path.name}")
        return

    # ©lyr に書き込む（既存は上書き）
    f.tags["©lyr"] = [lyrics]
    f.save()
    print(" ... done")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    # サブプロセスモードを argparse より先に捕捉する
    # （argparse は target を必須とするため、--_fetch だけでは解析失敗する）
    if len(sys.argv) == 3 and sys.argv[1] == "--_fetch":
        _run_fetch_subprocess(sys.argv[2])
        return

    parser = argparse.ArgumentParser(
        description="uta-net.com から歌詞を取得して .m4a に書き込む",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "target",
        type=Path,
        help=".m4a ファイル、またはディレクトリ（再帰しない）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="ファイル間のリクエスト待機秒数",
    )
    args = parser.parse_args()

    target: Path = args.target

    if target.is_dir():
        m4a_paths = sorted(target.rglob("*.m4a"))
        if not m4a_paths:
            sys.exit(f"  .m4a ファイルが見つかりません: {target}")
    else:
        m4a_paths = [target]

    for i, path in enumerate(m4a_paths):
        print(f"[{path.name}]")
        process_m4a(path)
        if i < len(m4a_paths) - 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
