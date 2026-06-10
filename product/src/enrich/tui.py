"""エンリッチメント処理の Chat TUI。

## レイアウト

    ┌──────────────────────────────────┐
    │  cdrdao-enrich                   │  Header
    ├──────────────────────────────────┤
    │  [Assistant] 3 ファイルを開始    │  ← bold white
    │  [Tool: search] 🔍 クエリ…  ▶   │  ← bright_black  クリックで展開
    │    ┌────────────────────────┐    │
    │    │ 検索結果テキスト全体   │    │  ← Collapsible の中身
    │    └────────────────────────┘    │
    │  [Tool: fetch]  🌐 https://…  ▶ │  ← URL はリンク
    │  [確認] EPCE-7933 / Track 1     │  ← bold yellow
    │    lyricist: NOBE               │
    │    根拠URL: https://...         │
    │    適用しますか？ [y/n]         │
    │  [You] y                        │  ← bold green
    ├──────────────────────────────────┤
    │  > _                             │  Input (dock=bottom)
    └──────────────────────────────────┘

## メッセージ種別と色

    ChatKind.ASSISTANT : bold white   — LLM の出力・状況説明
    ChatKind.TOOL      : bright_black — Tool 呼び出し（Collapsible で戻り値展開）
    ChatKind.CONFIRM   : bold yellow  — 承認プロンプト（根拠URL付き）
    ChatKind.USER      : bold green   — ユーザーの回答
    ChatKind.ERROR     : bold red     — エラー・スキップ

## ブロッキング連携

    Worker スレッドが承認 / ask_user を必要とするとき:
      1. threading.Event を作成
      2. post_message(WaitForInput(...)) を TUI に投げる
      3. event.wait() でブロック
    TUI 側:
      1. WaitForInput を受け取り Input を有効化
      2. ユーザーが Enter → event.result = value; event.set()
      3. Worker が再開
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.worker import get_current_worker
from textual.message import Message
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    Static,
)
from textual.containers import VerticalScroll

from src.enrich.agent import (
    AgentConfig,
    EnrichCache,
    TrackCredits,
    build_agent,
)
from src.enrich.writer import enrich_file, FileDiff


# ---------------------------------------------------------------------------
# メッセージ種別
# ---------------------------------------------------------------------------


class ChatKind(Enum):
    ASSISTANT = auto()
    TOOL      = auto()
    CONFIRM   = auto()
    USER      = auto()
    ERROR     = auto()


_KIND_STYLE: dict[ChatKind, str] = {
    ChatKind.ASSISTANT: "bold white",
    ChatKind.TOOL:      "bright_black",
    ChatKind.CONFIRM:   "bold yellow",
    ChatKind.USER:      "bold green",
    ChatKind.ERROR:     "bold red",
}

_KIND_PREFIX: dict[ChatKind, str] = {
    ChatKind.ASSISTANT: "Assistant",
    ChatKind.TOOL:      "Tool",
    ChatKind.CONFIRM:   "確認",
    ChatKind.USER:      "You",
    ChatKind.ERROR:     "Error",
}

_TOOL_ICON: dict[str, str] = {
    "search":   "🔍",
    "fetch":    "🌐",
    "ask_user": "❓",
}


# ---------------------------------------------------------------------------
# 遅延レンダリング Collapsible
#
# 展開するまで Static をマウントしない。
# Textual はレイアウト計算で全子ウィジェットの高さを再測定するため、
# 大量テキストの Static を常時持っていると展開数 × テキスト長に比例して
# UI スレッドがブロックされる。初回展開時にだけマウントすることで回避する。
# ---------------------------------------------------------------------------


class LazyToolCollapsible(Collapsible):
    """ツール結果を遅延マウントする Collapsible。

    展開するまで Static をマウントしない。
    _watch_collapsed を override して初回展開時だけ Contents にマウントする。
    """

    def __init__(self, result: str, title: object, **kwargs: object) -> None:
        super().__init__(title=title, collapsed=True, **kwargs)
        self._result = result
        self._loaded = False

    def _watch_collapsed(self, collapsed: bool) -> None:
        super()._watch_collapsed(collapsed)
        if not collapsed and not self._loaded:
            self._loaded = True
            contents = self.query_one(Collapsible.Contents)
            contents.mount(Static(self._result, classes="tool-result"))


# ---------------------------------------------------------------------------
# Worker → TUI メッセージ
# ---------------------------------------------------------------------------


class AppendMessage(Message):
    """ログ欄に通常メッセージを追記するよう TUI に依頼する。"""

    def __init__(self, kind: ChatKind, text: str) -> None:
        super().__init__()
        self.kind = kind
        self.text = text


class AppendToolMessage(Message):
    """ツール呼び出し行（Collapsible）を追記するよう TUI に依頼する。"""

    def __init__(self, kind_str: str, detail: str, result: str) -> None:
        super().__init__()
        self.kind_str = kind_str  # "search" | "fetch" | "ask_user"
        self.detail   = detail    # クエリ / URL / 質問文
        self.result   = result    # ツールの戻り値全文


class WaitForInput(Message):
    """ユーザー入力を待機するよう TUI に依頼する（ブロッキング）。"""

    def __init__(
        self,
        prompt: str,
        event: threading.Event,
        mode: str = "ask",           # "ask" | "confirm"
        credits: TrackCredits | None = None,
        disc_number: str = "",
        track_number: int = 0,
        title: str = "",
    ) -> None:
        super().__init__()
        self.prompt       = prompt
        self.event        = event
        self.mode         = mode
        self.credits      = credits
        self.disc_number  = disc_number
        self.track_number = track_number
        self.title        = title
        self.result: str  = ""


class WorkerDone(Message):
    """全ファイルの処理完了を通知する。"""

    def __init__(self, diffs: list[FileDiff]) -> None:
        super().__init__()
        self.diffs = diffs


# ---------------------------------------------------------------------------
# Chat TUI
# ---------------------------------------------------------------------------


class EnrichChatApp(App[list[FileDiff]]):
    """エンリッチメント処理の Chat UI。"""

    CSS = """
    #log-scroll {
        height: 1fr;
        padding: 0 1;
    }
    #chat-input {
        dock: bottom;
        border-top: solid $primary-darken-2;
    }
    /* 通常メッセージ行 */
    .chat-line {
        padding: 0;
        margin: 0;
    }
    /* ツール行の Collapsible */
    .tool-collapsible {
        margin: 0 0 0 0;
        padding: 0;
        background: $surface;
    }
    .tool-collapsible > CollapsibleTitle {
        color: $text-muted;
        padding: 0 1;
    }
    /* Collapsible 展開内容 */
    .tool-result {
        padding: 0 2;
        color: $text-muted;
        background: $surface-darken-1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "中断"),
        Binding("escape", "quit", "中断"),
    ]

    def __init__(
        self,
        yaml_paths: list[Path],
        config: AgentConfig,
        cache: EnrichCache,
        *,
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self._yaml_paths     = yaml_paths
        self._config         = config
        self._cache          = cache
        self._dry_run        = dry_run
        self._pending_input: WaitForInput | None = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="log-scroll"):
            pass  # メッセージを動的にマウントする
        yield Input(id="chat-input", placeholder="（処理中…）", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._append_line(ChatKind.ASSISTANT, f"{len(self._yaml_paths)} ファイルのエンリッチを開始します。")
        if self._dry_run:
            self._append_line(ChatKind.ASSISTANT, "[dry-run] ファイルは変更されません。")
        self._run_enrich()

    # ------------------------------------------------------------------
    # ウィジェットマウントヘルパー（メインスレッドから呼ぶ）
    # ------------------------------------------------------------------

    def _scroll(self) -> VerticalScroll:
        return self.query_one("#log-scroll", VerticalScroll)

    def _append_line(self, kind: ChatKind, text: str) -> None:
        """通常メッセージ行を追記する。"""
        style  = _KIND_STYLE[kind]
        prefix = _KIND_PREFIX[kind]
        rich_text = Text()
        rich_text.append(f"[{prefix}] ", style=style)
        rich_text.append(text, style="white" if kind == ChatKind.TOOL else style)
        widget = Static(rich_text, classes="chat-line")
        self._scroll().mount(widget)
        widget.scroll_visible()

    def _append_tool(self, kind_str: str, detail: str, result: str) -> None:
        """ツール呼び出し行を Collapsible で追記する。"""
        icon    = _TOOL_ICON.get(kind_str, "⚙")
        preview = detail if len(detail) <= 80 else detail[:77] + "…"

        # タイトル行テキスト
        title_text = Text()
        title_text.append(f"[Tool: {kind_str}] {icon} ", style=_KIND_STYLE[ChatKind.TOOL])
        if detail.startswith("http"):
            title_text.append(detail, style=f"link {detail} bright_black underline")
        else:
            title_text.append(preview, style="bright_black")

        # 展開内容: 初回展開時にのみマウントする（遅延レンダリング）
        collapsible = LazyToolCollapsible(
            result,
            title=title_text,
            classes="tool-collapsible",
        )
        self._scroll().mount(collapsible)
        collapsible.scroll_visible()

    def _append_confirm(
        self,
        credits: TrackCredits,
        disc_number: str,
        track_number: int,
        title: str,
    ) -> None:
        """承認プロンプトを追記する。"""
        scroll = self._scroll()

        # ヘッダ行
        header = Text()
        header.append("[確認] ", style=_KIND_STYLE[ChatKind.CONFIRM])
        header.append(f"{disc_number} / Track {track_number} 「{title}」", style="bold yellow")
        scroll.mount(Static(header, classes="chat-line"))

        # クレジット値
        for field in ("lyricist", "composer", "arranger", "singer"):
            val = getattr(credits, field)
            if val is not None:
                line = Text()
                line.append(f"  {field}: ", style="yellow")
                line.append(val, style="white")
                scroll.mount(Static(line, classes="chat-line"))

        # 根拠URL
        if credits.sources:
            scroll.mount(Static(Text("  根拠URL:", style="yellow"), classes="chat-line"))
            for url in credits.sources:
                url_line = Text(f"    {url}", style=f"link {url} cyan underline")
                scroll.mount(Static(url_line, classes="chat-line"))

        # 確認プロンプト
        prompt_line = Text()
        prompt_line.append("  この情報を適用しますか？ ", style="bold yellow")
        prompt_line.append("[y/Enter] 承認  [n] 却下  [c] コメントして再調査", style="dim yellow")
        last = Static(prompt_line, classes="chat-line")
        scroll.mount(last)
        last.scroll_visible()

    # ------------------------------------------------------------------
    # Worker → TUI メッセージハンドラ
    # ------------------------------------------------------------------

    def on_append_message(self, msg: AppendMessage) -> None:
        self._append_line(msg.kind, msg.text)

    def on_append_tool_message(self, msg: AppendToolMessage) -> None:
        self._append_tool(msg.kind_str, msg.detail, msg.result)

    def on_wait_for_input(self, msg: WaitForInput) -> None:
        self._pending_input = msg

        if msg.mode == "confirm":
            assert msg.credits is not None
            self._append_confirm(msg.credits, msg.disc_number, msg.track_number, msg.title)
            placeholder = "y/Enter で承認 / n で却下 / c でコメントして再調査"
        else:
            self._append_line(ChatKind.CONFIRM, f"[質問] {msg.prompt}")
            placeholder = "回答を入力して Enter"

        inp: Input = self.query_one("#chat-input", Input)
        inp.placeholder = placeholder
        inp.disabled    = False
        inp.focus()

    def on_worker_done(self, msg: WorkerDone) -> None:
        total   = len(msg.diffs)
        changed = sum(1 for d in msg.diffs if d.has_changes)
        skipped = sum(1 for d in msg.diffs if d.skipped)
        self._append_line(
            ChatKind.ASSISTANT,
            f"完了: {total} ファイル / 更新 {changed} / スキップ {skipped}",
        )
        if self._dry_run:
            self._append_line(ChatKind.ASSISTANT, "[dry-run] ファイルは変更されていません。")

        inp: Input = self.query_one("#chat-input", Input)
        inp.placeholder = "Enter で終了"
        inp.disabled    = False
        inp.focus()

    # ------------------------------------------------------------------
    # Input ハンドラ
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        inp: Input = self.query_one("#chat-input", Input)
        inp.clear()

        pending = self._pending_input
        if pending is None:
            self.exit(result=[])
            return

        self._append_line(ChatKind.USER, value or "(Enter)")

        if pending.mode == "confirm":
            if value.lower() in ("", "y", "yes"):
                # 承認
                pending.result = "y"
                self._pending_input = None
                inp.disabled    = True
                inp.placeholder = "（処理中…）"
                pending.event.set()
            elif value.lower() in ("c", "comment"):
                # コメント入力モードへ遷移（event はまだ set しない）
                pending.mode    = "confirm_comment"
                inp.placeholder = "修正コメントを入力して Enter（再調査を依頼）"
                inp.disabled    = False
                inp.focus()
            else:
                # 却下（n またはその他）
                pending.result  = "n"
                self._pending_input = None
                inp.disabled    = True
                inp.placeholder = "（処理中…）"
                pending.event.set()

        elif pending.mode == "confirm_comment":
            # コメントを result に格納して再試行を促す
            pending.result  = value  # 空文字でも可（→ agent 側で "" は却下扱い）
            self._pending_input = None
            inp.disabled    = True
            inp.placeholder = "（処理中…）"
            pending.event.set()

        else:
            # ask モード
            pending.result  = value
            self._pending_input = None
            inp.disabled    = True
            inp.placeholder = "（処理中…）"
            pending.event.set()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    @work(thread=True)
    def _run_enrich(self) -> None:
        """全ファイルのエンリッチ処理をバックグラウンドスレッドで実行する。"""

        def on_tool_call(kind_str: str, detail: str, result: str) -> None:
            self.post_message(AppendToolMessage(kind_str, detail, result))

        def ask_user_fn(question: str) -> str:
            event    = threading.Event()
            wait_msg = WaitForInput(prompt=question, event=event, mode="ask")
            if not self.post_message(wait_msg):
                return ""  # アプリが閉じかけている
            worker = get_current_worker()
            while not event.wait(timeout=0.25):
                if worker.is_cancelled:
                    return ""
            return wait_msg.result

        def confirm_fn(
            credits: TrackCredits,
            disc_number: str,
            track_number: int,
            title: str,
        ) -> str | None:
            event    = threading.Event()
            wait_msg = WaitForInput(
                prompt="",
                event=event,
                mode="confirm",
                credits=credits,
                disc_number=disc_number,
                track_number=track_number,
                title=title,
            )
            if not self.post_message(wait_msg):
                return ""  # アプリが閉じかけている → 却下扱い
            worker = get_current_worker()
            while not event.wait(timeout=0.25):
                if worker.is_cancelled:
                    return ""
            if wait_msg.result == "y":
                return None   # 承認
            return wait_msg.result  # "" = 却下 / それ以外 = コメント付き再試行

        agent = build_agent(
            self._config,
            on_tool_call=on_tool_call,
            ask_user_fn=ask_user_fn,
        )

        diffs: list[FileDiff] = []
        for path in self._yaml_paths:
            self.post_message(AppendMessage(ChatKind.ASSISTANT, f"処理中: {path.name}"))
            diff = enrich_file(
                path,
                agent,
                self._cache,
                confirm_fn=confirm_fn,
                dry_run=self._dry_run,
                on_log=lambda kind, text: self.post_message(AppendMessage(kind, text)),
            )
            diffs.append(diff)

            if diff.skipped:
                self.post_message(AppendMessage(
                    ChatKind.ASSISTANT,
                    f"スキップ: {path.name} ({diff.skip_reason})",
                ))
            elif diff.has_changes:
                self.post_message(AppendMessage(
                    ChatKind.ASSISTANT,
                    f"更新: {path.name} — {diff.changed_count} トラック補完",
                ))

        self.post_message(WorkerDone(diffs))


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def run_enrich_tui(
    yaml_paths: list[Path],
    config: AgentConfig,
    cache: EnrichCache,
    *,
    dry_run: bool = False,
) -> list[FileDiff]:
    """エンリッチ Chat TUI を起動し、処理結果を返す。"""
    app = EnrichChatApp(yaml_paths, config, cache, dry_run=dry_run)
    return app.run() or []
