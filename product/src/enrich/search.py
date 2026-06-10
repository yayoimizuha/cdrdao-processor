"""Yahoo! JAPAN 検索による Web 検索実装。

公開 API の search() はサブプロセスで自身を起動することで
Playwright / sync_playwright を GIL の外に追い出し、TUI のフリーズを防ぐ。

__main__ として起動された場合は実際のブラウザ操作を行い、
結果を JSON {"ok": true, "results": [...]} または {"ok": false, "error": "..."}
として stdout に書き出して終了する。

インターフェース:
    search(query: str) -> list[str]
        各要素: "# [タイトル](URL)\nスニペット"
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# product/ ディレクトリ（-m src.enrich.search が解決できるパッケージルート）
_PROJECT_ROOT: Path = Path(__file__).parents[2]

_MAX_RESULTS = 10

# サブプロセスのタイムアウト（秒）
_SUBPROCESS_TIMEOUT: int = 45


def search(query: str) -> list[str]:
    """Yahoo! JAPAN で query を検索し、結果テキストのリストを返す。

    内部でサブプロセス（python -m src.enrich.search <query>）を起動し、
    Playwright を GIL の外で実行することで TUI スレッドのフリーズを防ぐ。

    各要素は Markdown 形式:
        # [タイトル](URL)
        スニペット（あれば）

    Args:
        query: 検索クエリ文字列。

    Returns:
        検索結果テキストのリスト（最大 _MAX_RESULTS 件）。
    """
    result = subprocess.run(
        [sys.executable, "-m", "src.enrich.search", query],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=_PROJECT_ROOT,
    )
    if not result.stdout.strip():
        stderr_msg = result.stderr.strip() if result.stderr else "(no output)"
        raise RuntimeError(f"search subprocess returned no output (exit {result.returncode}): {stderr_msg}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"search subprocess returned invalid JSON: {result.stdout[:200]}") from exc
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "search: unknown error"))
    return payload["results"]


# ---------------------------------------------------------------------------
# __main__: サブプロセスとして呼ばれたときのブラウザ実装
# ---------------------------------------------------------------------------

_SEARCH_URL = "https://search.yahoo.co.jp/search?p={query}&qrw=0&b=1"

# 結果カード出現を待つセレクタ（固定 ms 待機より確実）
_RESULT_SELECTOR = "div.sw-Card.Algo"

# セレクタ待機のタイムアウト（ミリ秒）
_SELECTOR_TIMEOUT_MS = 10_000


def _run_search(query: str) -> None:
    """ブラウザで検索を実行して JSON を stdout に書き出す。"""
    import urllib.parse

    from bs4 import BeautifulSoup
    from fake_useragent import UserAgent
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    url = _SEARCH_URL.format(query=urllib.parse.quote_plus(query))
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
                # 画像・メディア・フォントはブロックして高速化
                page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in {"image", "media", "font"}
                        else route.continue_()
                    ),
                )
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                # 固定 ms 待機の代わりに結果カードが出現するまで待つ
                try:
                    page.wait_for_selector(_RESULT_SELECTOR, timeout=_SELECTOR_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    pass  # カードが現れなくても空リストとして続行
                html = page.content()
            finally:
                browser.close()

        soup = BeautifulSoup(html, "lxml")

        results: list[str] = []
        # 各検索結果は class="sw-Card Algo" の div
        for card in soup.find_all("div", class_=lambda c: c and "sw-Card" in c and "Algo" in c):
            # タイトルと URL: h3.sw-Card__titleMain を含む a タグ
            title_tag = card.find("h3", class_=lambda c: c and "sw-Card__titleMain" in c)
            if title_tag is None:
                continue

            anchor = title_tag.find_parent("a")
            if anchor is None:
                continue

            title = title_tag.get_text(strip=True)
            href = anchor.get("href", "").split("#")[0]

            # スニペット: カード内の p タグ
            p_tag = card.find("p")
            snippet = p_tag.get_text(strip=True) if p_tag else ""

            entry = f"# [{title}]({href})"
            if snippet:
                entry += f"\n{snippet}"
            results.append(entry)

            if len(results) >= _MAX_RESULTS:
                break

        print(json.dumps({"ok": True, "results": results}))

    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: search.py <query>"}), flush=True)
        sys.exit(1)
    _run_search(sys.argv[1])
