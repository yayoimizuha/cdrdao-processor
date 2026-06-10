"""textual ベースの TUI — 候補選択・曲名補完・手動入力。

3 画面構成:
  1. DiscCandidate 選択
  2. 未解決曲名の手入力
  3. 未登録ディスクの手動入力 (レジストリ不一致時)
"""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.validation import Length
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from src.models import (
    DiscCandidate,
    DiscRecord,
    ResolvedTitle,
    TrackRecord,
)

# ── キャンセル時の終了コード ─────────────────────────────────────────────

CANCEL_EXIT_CODE = 5

# ── 手動入力へ切り替えるセンチネル ───────────────────────────────────────

_MANUAL = object()


# =========================================================================
# 画面 1 — 候補選択
# =========================================================================


class CandidateSelectApp(App[DiscCandidate | None | object]):
    """候補リストから :class:`DiscCandidate` を 1 つ選択する。"""

    TITLE = "ディスク候補を選択"
    BINDINGS = [
        Binding("escape", "cancel", "キャンセル"),
        Binding("q", "cancel", "キャンセル"),
        Binding("m", "manual", "手動入力"),
    ]

    def __init__(
        self,
        candidates: list[DiscCandidate],
        toc_name: str = "",
        resolved_count: int = 0,
        total_count: int = 0,
    ) -> None:
        super().__init__()
        self._candidates = candidates
        self._toc_name = toc_name
        self._resolved_count = resolved_count
        self._total_count = total_count

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"  TOC: {self._toc_name}   "
            f"解決済み曲名: {self._resolved_count}/{self._total_count} トラック"
        )
        table = DataTable(id="cand_table")
        table.cursor_type = "row"
        table.add_columns("品番", "リリースタイトル", "スコア", "アーティスト", "発売日")
        for c in self._candidates:
            table.add_row(
                c.disc_number,
                c.release_title or "",
                f"{c.score:.3f}",
                c.artist or "",
                c.release_date or "",
            )
        yield table
        yield Footer()

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        if 0 <= event.cursor_row < len(self._candidates):
            self.exit(self._candidates[event.cursor_row])

    def action_cancel(self) -> None:
        self.exit(None)

    def action_manual(self) -> None:
        self.exit(_MANUAL)


def select_candidate(
    candidates: list[DiscCandidate],
    toc_name: str = "",
    resolved_count: int = 0,
    total_count: int = 0,
) -> DiscCandidate | None | object:
    """候補選択 TUI を実行し、選択された候補・``None``（キャンセル）・``_MANUAL`` を返す。"""
    app = CandidateSelectApp(candidates, toc_name, resolved_count, total_count)
    return app.run()


# =========================================================================
# 画面 2 — 未解決曲名の手入力
# =========================================================================


class TitleInputApp(App[list[ResolvedTitle] | None]):
    """未解決トラックの曲名を入力させる。"""

    TITLE = "未解決の曲名を入力"
    BINDINGS = [
        Binding("escape", "cancel", "スキップして続行"),
        Binding("q", "cancel", "スキップして続行", priority=False),
    ]

    def __init__(self, resolved: list[ResolvedTitle], toc_name: str = "") -> None:
        super().__init__()
        self._resolved = list(resolved)
        self._toc_name = toc_name
        # 入力が必要なインデックス
        self._missing_idx = [
            i for i, r in enumerate(self._resolved) if r.title is None
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"  TOC: {self._toc_name}\n"
            "  ISRC のない、または検索が空振りしたトラックの曲名を入力してください。\n"
            "  空のままにすると、そのトラックはスコアリングから除外されます。"
        )
        with VerticalScroll():
            for idx in self._missing_idx:
                r = self._resolved[idx]
                isrc_str = r.isrc if r.isrc else "(なし)"
                yield Label(f"  Track {r.track_number:02d}  ISRC: {isrc_str}")
                yield Input(
                    placeholder="曲名を入力…",
                    id=f"title_{idx}",
                )
        yield Footer()

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        # 最後のフィールドで Enter → 確定
        inputs = self.query(Input)
        input_list = list(inputs)
        if event.input == input_list[-1]:
            self._apply_and_exit()
        else:
            # 次の入力欄にフォーカス
            cur = input_list.index(event.input)
            if cur + 1 < len(input_list):
                input_list[cur + 1].focus()

    def _apply_and_exit(self) -> None:
        for idx in self._missing_idx:
            widget = self.query_one(f"#title_{idx}", Input)
            val = widget.value.strip()
            if val:
                self._resolved[idx] = ResolvedTitle(
                    track_number=self._resolved[idx].track_number,
                    isrc=self._resolved[idx].isrc,
                    title=val,
                    manually_entered=True,
                )
        self.exit(self._resolved)

    def action_cancel(self) -> None:
        # 変更なしの元リストを返す
        self.exit(None)


def input_missing_titles(
    resolved: list[ResolvedTitle], toc_name: str = ""
) -> list[ResolvedTitle] | None:
    """曲名入力 TUI を実行する。更新済みリストまたはスキップ時 ``None`` を返す。"""
    app = TitleInputApp(resolved, toc_name=toc_name)
    return app.run()


# =========================================================================
# 画面 3 — 未登録ディスクの手動入力 (レジストリ不一致)
# =========================================================================


class ManualDiscApp(App[DiscRecord | None]):
    """レジストリに該当がない場合にディスク情報を手動入力させる。"""

    TITLE = "レジストリ未登録: ディスク情報を入力"
    BINDINGS = [
        Binding("escape", "cancel", "スキップして終了"),
        Binding("q", "cancel", "スキップして終了", priority=False),
    ]

    def __init__(self, resolved: list[ResolvedTitle], toc_name: str = "") -> None:
        super().__init__()
        self._resolved = resolved
        self._toc_name = toc_name

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"  TOC: {self._toc_name}\n"
            "  このディスクはレジストリに見つかりませんでした。\n"
            "  情報を入力すると出力ファイルを生成できます。空欄は null として出力されます。"
        )
        with VerticalScroll():
            yield Label("品番 (Disc Number) [必須]")
            yield Input(id="disc_number", validators=[Length(minimum=1)])
            yield Label("リリースタイトル")
            yield Input(id="release_title")
            yield Label("アーティスト")
            yield Input(id="artist")
            yield Label("発売日 (YYYY-MM-DD)")
            yield Input(id="release_date")
            yield Label("レーベル")
            yield Input(id="label")
            yield Label("リリース種別 (single / album)")
            yield Input(id="release_type")
            yield Label("盤種")
            yield Input(id="disc_type")

            yield Static("  ── トラック ──")
            for r in self._resolved:
                isrc_str = r.isrc or "(なし)"
                default = r.title or ""
                yt_status = f'yt-dlp: "{r.title}"' if r.title else "yt-dlp: (空振り)"
                yield Label(
                    f"  Track {r.track_number:02d}  ISRC: {isrc_str}  {yt_status}"
                )
                yield Input(
                    value=default,
                    placeholder="曲名",
                    id=f"track_{r.track_number}",
                )
        yield Footer()

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        inputs = list(self.query(Input))
        if event.input == inputs[-1]:
            self._try_confirm()
        else:
            cur = inputs.index(event.input)
            if cur + 1 < len(inputs):
                inputs[cur + 1].focus()

    def _try_confirm(self) -> None:
        disc_number = self.query_one("#disc_number", Input).value.strip()
        if not disc_number:
            self.notify("品番は必須です", severity="error")
            self.query_one("#disc_number", Input).focus()
            return

        def _val(widget_id: str) -> str | None:
            v = self.query_one(f"#{widget_id}", Input).value.strip()
            return v if v else None

        track_records: list[TrackRecord] = []
        for r in self._resolved:
            title_val = self.query_one(f"#track_{r.track_number}", Input).value.strip()
            track_records.append(
                TrackRecord(order=r.track_number, title=title_val or "")
            )

        disc = DiscRecord(
            disc_number=disc_number,
            release_title=_val("release_title"),
            artist=_val("artist"),
            release_date=_val("release_date"),
            label=_val("label"),
            release_type=_val("release_type"),
            disc_type=_val("disc_type"),
            tracks=track_records,
        )
        self.exit(disc)

    def action_cancel(self) -> None:
        self.exit(None)


def input_manual_disc(
    resolved: list[ResolvedTitle], toc_name: str = ""
) -> DiscRecord | None:
    """手動入力 TUI を実行する。:class:`DiscRecord` またはキャンセル時 ``None`` を返す。"""
    app = ManualDiscApp(resolved, toc_name=toc_name)
    return app.run()
