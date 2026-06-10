"""Playwright を使った URL フェッチ + Markdown 変換モジュール。

公開 API の fetch_page() はサブプロセスで自身を起動することで
Playwright / sync_playwright を GIL の外に追い出し、TUI のフリーズを防ぐ。

__main__ として起動された場合は実際のブラウザ操作を行い、
結果を JSON {"ok": true, "text": "..."} または {"ok": false, "error": "..."}
として stdout に書き出して終了する。

<details> 要素は JS で強制展開してから取得する。
LLM に渡すトークン量を抑えるため、取得テキストは MAX_CHARS 文字で打ち切る。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Final

# product/ ディレクトリ（-m src.enrich.fetch が解決できるパッケージルート）
_PROJECT_ROOT: Path = Path(__file__).parents[2]

# LLM コンテキストへ渡す最大文字数（超過分はトリミング）
MAX_CHARS: Final[int] = 8000

# ページ読み込みタイムアウト（ミリ秒）
PAGE_TIMEOUT_MS: Final[int] = 20_000

# load イベント後の JS レンダリング完了待機（ミリ秒）
WAIT_AFTER_LOAD_MS: Final[int] = 1_500

# サブプロセスのタイムアウト（秒）— PAGE_TIMEOUT_MS より十分大きく取る
_SUBPROCESS_TIMEOUT: Final[int] = 60


def fetch_page(url: str) -> str:
    """URL のページ内容を取得して Markdown テキストで返す。

    内部でサブプロセス（python -m src.enrich.fetch <url>）を起動し、
    Playwright を GIL の外で実行することで TUI スレッドのフリーズを防ぐ。

    Args:
        url: 取得する URL。

    Returns:
        ページ本文の Markdown テキスト（最大 MAX_CHARS 文字）。

    Raises:
        RuntimeError: ページ取得に失敗した場合。
    """
    result = subprocess.run(
        [sys.executable, "-m", "src.enrich.fetch", url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=_PROJECT_ROOT,
    )
    payload = json.loads(result.stdout)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "fetch_page: unknown error"))
    return payload["text"]


# ---------------------------------------------------------------------------
# __main__: サブプロセスとして呼ばれたときのブラウザ実装
# ---------------------------------------------------------------------------

def _run_fetch(url: str) -> None:
    """ブラウザでページを取得して JSON を stdout に書き出す。"""
    import html2text
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()

                # 画像・フォント・メディアはブロックしてロードを高速化
                page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in {"image", "media", "font"}
                        else route.continue_()
                    ),
                )

                # networkidle はポーリング通信があるサイトでタイムアウトするため使わない
                # load (window.onload) + 固定待機で JS レンダリングを待つ
                page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="load")
                page.wait_for_timeout(WAIT_AFTER_LOAD_MS)

                # <details> 要素を JS で強制的にすべて open にする
                page.evaluate(
                    "document.querySelectorAll('details').forEach(el => el.open = true)"
                )

                html_content = page.content()
            finally:
                browser.close()

        # readability で本文を抽出してからMarkdown変換する。
        # ナビゲーション・メニュー等のノイズを除去し、
        # MAX_CHARS に本文が収まりやすくなる。
        # 抽出結果が極端に短い場合はフォールバックとして <body> 全体を使う。
        from readability import Document  # noqa: PLC0415

        doc = Document(html_content)
        main_html = doc.summary(html_partial=True)

        # readability が実質的な本文を返せなかった場合のフォールバック
        # （タイトルのみ・空など）
        _FALLBACK_THRESHOLD = 500  # 文字数
        if len(main_html) < _FALLBACK_THRESHOLD:
            main_html = html_content

        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.ignore_emphasis = False
        converter.body_width = 0  # 折り返しなし

        text = converter.handle(main_html)

        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n\n... (truncated at {MAX_CHARS} chars)"

        print(json.dumps({"ok": True, "text": text}))

    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: fetch.py <url>"}), flush=True)
        sys.exit(1)
    _run_fetch(sys.argv[1])
