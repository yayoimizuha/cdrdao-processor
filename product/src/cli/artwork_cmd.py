"""cdrdao-artwork エントリポイント。

Gradio ベースのブラウザ UI でアルバムアートワークを検索・確認し、
YAML メタデータの disc.artwork_url フィールドに保存する。

2 つのモードを提供する:
  - 個別モード: YAML を選択して検索・付与
  - 一括モード: 未設定の YAML を順番に処理（スキップ・次へ対応）

使用例:
    uv run cdrdao-artwork
    uv run cdrdao-artwork --dir metadatas/
    uv run cdrdao-artwork --port 7861
"""

import argparse
import sys
from pathlib import Path

_DEFAULT_METADATAS_DIR = Path(__file__).parents[2] / "metadatas"
_DEFAULT_PORT = 7860


# ---------------------------------------------------------------------------
# Gradio UI 定義
# ---------------------------------------------------------------------------


def _build_app(metadatas_dir: Path):  # noqa: ANN001
    """Gradio アプリを構築して返す。"""
    import gradio as gr

    from src.artwork.itunes import ArtworkResult, search_artwork, search_artwork_by_query
    from src.artwork.writer import YamlMeta, list_yaml_files, read_artwork_url, write_artwork_url

    # -----------------------------------------------------------------------
    # 状態管理ヘルパー
    # -----------------------------------------------------------------------

    def _reload_file_list() -> list[YamlMeta]:
        return list_yaml_files(metadatas_dir)

    def _display_names(metas: list[YamlMeta]) -> list[str]:
        return [m.display_name for m in metas]

    def _find_meta(metas: list[YamlMeta], display_name: str) -> YamlMeta | None:
        for m in metas:
            if m.display_name == display_name:
                return m
        return None

    # -----------------------------------------------------------------------
    # 検索・選択ロジック
    # -----------------------------------------------------------------------

    def do_search(
        query: str,
        album_hint: str,
        artist_hint: str,
    ) -> tuple[list[tuple[str, str]], list[ArtworkResult]]:
        """iTunes API で検索してギャラリー用データと生データを返す。"""
        q = query.strip()
        if not q:
            q = f"{artist_hint} {album_hint}".strip()
        if not q:
            return [], []

        try:
            results = search_artwork_by_query(q, limit=12)
        except Exception as exc:  # noqa: BLE001
            return [], []

        # Gradio Gallery は (画像URL, キャプション) のタプルリストを受け取る
        gallery_items = [(r.url_100, r.display_label) for r in results]
        return gallery_items, results

    # -----------------------------------------------------------------------
    # UI レイアウト
    # -----------------------------------------------------------------------

    # ギャラリーを1行横並びにし、キャプションをホバー時に全文表示する
    _GALLERY_CSS = """
/* grid-wrap を flex 化して画像を縦いっぱいに引き伸ばす */
.gallery-container .grid-wrap {
    display: flex !important;
    align-items: stretch !important;
    overflow-x: auto !important;
    overflow-y: hidden !important;
    padding: var(--size-2) !important;
    height: 100% !important;
    box-sizing: border-box !important;
}
.gallery-container .grid-container {
    display: flex !important;
    flex-direction: row !important;
    align-items: stretch !important;
    gap: var(--spacing-lg) !important;
    height: 100% !important;
    width: max-content !important;
}
.gallery-container .gallery-item {
    height: 100% !important;
    width: auto !important;
    aspect-ratio: 1 / 1 !important;
}
.gallery-container .thumbnail-item {
    height: 100% !important;
    width: auto !important;
    aspect-ratio: 1 / 1 !important;
}
.gallery-container .thumbnail-item img {
    height: 100% !important;
    width: 100% !important;
    object-fit: contain !important;
}
/* caption-label: 通常は truncate 表示、hover 時に全文をツールチップ風に展開 */
.gallery-container .caption-label {
    position: absolute !important;
    right: 0 !important;
    bottom: 0 !important;
    left: 0 !important;
    max-width: 100% !important;
    border-radius: 0 !important;
    background: rgba(0,0,0,0.55) !important;
    color: #fff !important;
    font-size: 10px !important;
    padding: 2px 4px !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    opacity: 0;
    transition: opacity 0.15s;
}
.gallery-container .thumbnail-item:hover .caption-label {
    opacity: 1 !important;
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
    z-index: 100 !important;
    bottom: 0 !important;
    word-break: break-all !important;
}
"""

    _GALLERY_JS = ""

    with gr.Blocks(title="アートワーク付与ツール") as app:
        # --- 共有状態 ---
        state_metas = gr.State([])          # list[YamlMeta]
        state_results = gr.State([])        # list[ArtworkResult] 最新検索結果
        state_selected_url = gr.State("")   # 現在選択中のアートワーク URL (600px)
        state_batch_index = gr.State(0)     # 一括モードの現在インデックス

        gr.Markdown("# アートワーク付与ツール")
        gr.Markdown(
            "iTunes Search API でアルバムアートワークを検索し、"
            "YAML メタデータの `disc.artwork_url` に保存します。"
        )

        # ===================================================================
        # タブ 1: 個別モード
        # ===================================================================
        with gr.Tab("個別モード"):
            with gr.Row():
                # --- 左ペイン: ファイル一覧 ---
                with gr.Column(scale=1):
                    gr.Markdown("### YAML ファイル一覧")
                    btn_refresh = gr.Button("一覧を更新", variant="secondary", size="sm")
                    radio_files = gr.Radio(
                        label="ファイルを選択",
                        choices=[],
                        value=None,
                    )
                    txt_current_url = gr.Textbox(
                        label="現在の artwork_url",
                        interactive=False,
                        placeholder="未設定",
                        lines=2,
                    )

                # --- 中央・右ペイン: 検索 ---
                with gr.Column(scale=3):
                    gr.Markdown("### アートワーク検索")

                    with gr.Row():
                        txt_search_query = gr.Textbox(
                            label="検索クエリ（自動生成・編集可）",
                            placeholder="アーティスト名 アルバム名",
                            scale=4,
                        )
                        btn_search = gr.Button("検索", variant="primary", scale=1)

                    # hidden hints（ファイル選択時に自動設定）
                    txt_album_hint = gr.Textbox(visible=False)
                    txt_artist_hint = gr.Textbox(visible=False)

                    gallery = gr.Gallery(
                        label="検索結果（クリックして選択）",
                        columns=12,
                        height=200,
                        object_fit="contain",
                        show_label=True,
                        allow_preview=False,
                        selected_index=None,
                        fit_columns=False,
                        elem_classes="gallery-container",
                    )

                    with gr.Row():
                        with gr.Column(scale=2):
                            img_preview = gr.Image(
                                label="選択中のアートワーク（高解像度）",
                                height=300,
                                buttons=[],
                            )
                        with gr.Column(scale=3):
                            txt_selected_url = gr.Textbox(
                                label="選択中の URL（直接編集可）",
                                placeholder="https://is1-ssl.mzstatic.com/...",
                                lines=2,
                            )
                            with gr.Row():
                                btn_preview_manual = gr.Button(
                                    "URLをプレビュー", variant="secondary"
                                )
                                btn_save = gr.Button(
                                    "YAMLに保存", variant="primary"
                                )
                                btn_clear = gr.Button(
                                    "クリア", variant="stop"
                                )
                            txt_save_status = gr.Textbox(
                                label="保存結果",
                                interactive=False,
                                lines=1,
                            )

        # ===================================================================
        # タブ 2: 一括モード
        # ===================================================================
        with gr.Tab("一括モード"):
            gr.Markdown("### 未設定のファイルを順番に処理")
            gr.Markdown(
                "アートワーク未設定の YAML を順番に表示します。  \n"
                "「保存して次へ」で保存しながら進み、「スキップ」で保存せず次のファイルへ進みます。"
            )

            with gr.Row():
                lbl_batch_progress = gr.Markdown("ファイルを読み込んでください")
                btn_batch_start = gr.Button("一括処理を開始", variant="primary")

            with gr.Row():
                # --- 左ペイン: 情報表示 ---
                with gr.Column(scale=1):
                    gr.Markdown("### 現在のファイル")
                    txt_batch_disc = gr.Textbox(label="品番", interactive=False)
                    txt_batch_title = gr.Textbox(label="タイトル", interactive=False)
                    txt_batch_artist = gr.Textbox(label="アーティスト", interactive=False)
                    txt_batch_current_url = gr.Textbox(
                        label="現在の artwork_url",
                        interactive=False,
                        placeholder="未設定",
                        lines=2,
                    )

                # --- 右ペイン: 検索・選択 ---
                with gr.Column(scale=3):
                    gr.Markdown("### アートワーク検索")

                    with gr.Row():
                        txt_batch_query = gr.Textbox(
                            label="検索クエリ",
                            placeholder="アーティスト名 アルバム名",
                            scale=4,
                        )
                        btn_batch_search = gr.Button("検索", variant="primary", scale=1)

                    # hidden hints
                    txt_batch_album_hint = gr.Textbox(visible=False)
                    txt_batch_artist_hint = gr.Textbox(visible=False)
                    # 現在処理中のファイルパス
                    txt_batch_path = gr.Textbox(visible=False)

                    gallery_batch = gr.Gallery(
                        label="検索結果（クリックして選択）",
                        columns=12,
                        height=200,
                        object_fit="contain",
                        allow_preview=False,
                        selected_index=None,
                        fit_columns=False,
                        elem_classes="gallery-container",
                    )

                    with gr.Row():
                        with gr.Column(scale=2):
                            img_batch_preview = gr.Image(
                                label="選択中のアートワーク",
                                height=250,
                                buttons=[],
                            )
                        with gr.Column(scale=3):
                            txt_batch_selected_url = gr.Textbox(
                                label="選択中の URL（直接編集可）",
                                placeholder="https://is1-ssl.mzstatic.com/...",
                                lines=2,
                            )
                            with gr.Row():
                                btn_batch_preview_manual = gr.Button(
                                    "URLをプレビュー", variant="secondary"
                                )
                                btn_batch_save_next = gr.Button(
                                    "保存して次へ", variant="primary"
                                )
                                btn_batch_skip = gr.Button(
                                    "スキップ", variant="secondary"
                                )
                            txt_batch_status = gr.Textbox(
                                label="状態",
                                interactive=False,
                                lines=1,
                            )

        # ===================================================================
        # イベントハンドラ — 個別モード
        # ===================================================================

        def on_refresh():
            metas = _reload_file_list()
            choices = _display_names(metas)
            return metas, gr.update(choices=choices, value=None), ""

        btn_refresh.click(
            on_refresh,
            outputs=[state_metas, radio_files, txt_current_url],
        )

        def on_file_select(display_name: str, metas: list[YamlMeta]):
            if not display_name or not metas:
                return "", "", "", "", None
            meta = _find_meta(metas, display_name)
            if meta is None:
                return "", "", "", "", None
            query = f"{meta.artist} {meta.release_title}".strip()
            current = meta.artwork_url or ""
            return (
                query,
                meta.release_title,
                meta.artist,
                current,
                current or None,
            )

        radio_files.change(
            on_file_select,
            inputs=[radio_files, state_metas],
            outputs=[
                txt_search_query,
                txt_album_hint,
                txt_artist_hint,
                txt_current_url,
                txt_selected_url,
            ],
        )

        def on_search(query: str, album_hint: str, artist_hint: str):
            items, results = do_search(query, album_hint, artist_hint)
            first_url = results[0].url_hires if results else ""
            first_img = first_url if first_url else None
            return (
                gr.update(value=items),
                results,
                first_img,
                first_url,
            )

        btn_search.click(
            on_search,
            inputs=[txt_search_query, txt_album_hint, txt_artist_hint],
            outputs=[gallery, state_results, img_preview, txt_selected_url],
        )

        # Enterキーでも検索できるようにする
        txt_search_query.submit(
            on_search,
            inputs=[txt_search_query, txt_album_hint, txt_artist_hint],
            outputs=[gallery, state_results, img_preview, txt_selected_url],
        )

        def on_gallery_select(results: list[ArtworkResult], evt: gr.SelectData) -> tuple[str, str | None]:
            if not results or evt.index >= len(results):
                return "", None
            r = results[evt.index]
            return r.url_hires, r.url_hires

        gallery.select(
            on_gallery_select,
            inputs=[state_results],
            outputs=[txt_selected_url, img_preview],
        )

        def on_preview_manual(url: str):
            url = url.strip()
            if not url:
                return None
            return url

        btn_preview_manual.click(
            on_preview_manual,
            inputs=[txt_selected_url],
            outputs=[img_preview],
        )

        def on_save(
            display_name: str,
            metas: list[YamlMeta],
            url: str,
        ) -> tuple[str, str, list[YamlMeta], list[str]]:
            if not display_name or not metas:
                return "ファイルを選択してください", gr.update(), metas, gr.update()
            meta = _find_meta(metas, display_name)
            if meta is None:
                return "ファイルが見つかりません", gr.update(), metas, gr.update()
            url = url.strip()
            try:
                write_artwork_url(meta.path, url or None)
            except Exception as exc:  # noqa: BLE001
                return f"保存エラー: {exc}", gr.update(), metas, gr.update()

            # 一覧を更新
            new_metas = _reload_file_list()
            new_choices = _display_names(new_metas)
            # 保存後の表示名を特定して再選択
            new_meta = next((m for m in new_metas if m.path == meta.path), None)
            new_display = new_meta.display_name if new_meta else display_name
            status = f"保存しました: {meta.disc_number}"
            return (
                status,
                url,
                new_metas,
                gr.update(choices=new_choices, value=new_display),
            )

        btn_save.click(
            on_save,
            inputs=[radio_files, state_metas, txt_selected_url],
            outputs=[txt_save_status, txt_current_url, state_metas, radio_files],
        )

        def on_clear(display_name: str, metas: list[YamlMeta]):
            if not display_name or not metas:
                return "ファイルを選択してください", gr.update(), metas, gr.update()
            meta = _find_meta(metas, display_name)
            if meta is None:
                return "ファイルが見つかりません", gr.update(), metas, gr.update()
            try:
                write_artwork_url(meta.path, None)
            except Exception as exc:  # noqa: BLE001
                return f"クリアエラー: {exc}", gr.update(), metas, gr.update()
            new_metas = _reload_file_list()
            new_choices = _display_names(new_metas)
            new_meta = next((m for m in new_metas if m.path == meta.path), None)
            new_display = new_meta.display_name if new_meta else display_name
            return (
                f"クリアしました: {meta.disc_number}",
                "",
                new_metas,
                gr.update(choices=new_choices, value=new_display),
            )

        btn_clear.click(
            on_clear,
            inputs=[radio_files, state_metas],
            outputs=[txt_save_status, txt_current_url, state_metas, radio_files],
        )

        # ===================================================================
        # イベントハンドラ — 一括モード
        # ===================================================================

        def _batch_pending(metas: list[YamlMeta]) -> list[YamlMeta]:
            """アートワーク未設定の YamlMeta を返す。"""
            return [m for m in metas if not m.has_artwork]

        def _batch_load_item(
            metas: list[YamlMeta],
            index: int,
        ) -> tuple:
            """一括モードの index 番目のファイルを UI に表示する値群を返す。"""
            pending = _batch_pending(metas)
            total = len(pending)

            if total == 0:
                return (
                    "**完了: 未設定のファイルはありません**",
                    index,
                    "", "", "", "",  # disc / title / artist / current_url
                    "", "", "",      # path / query / hints
                    gr.update(value=[], selected_index=None), [],   # gallery / results
                    None, "",        # preview / selected_url
                    "すべて処理済み",
                )

            idx = min(index, total - 1)
            meta = pending[idx]
            query = f"{meta.artist} {meta.release_title}".strip()
            progress = f"**{idx + 1} / {total} 件処理中** — {meta.disc_number}"

            return (
                progress,
                idx,
                meta.disc_number,
                meta.release_title,
                meta.artist,
                meta.artwork_url or "",
                str(meta.path),
                query,
                meta.release_title,
                meta.artist,
                    gr.update(value=[], selected_index=None), [],   # gallery / results
                None, "",  # preview / url クリア
                "",        # status
            )

        def on_batch_start(metas: list[YamlMeta]):
            if not metas:
                metas = _reload_file_list()
            return _batch_load_item(metas, 0) + (metas,)

        btn_batch_start.click(
            on_batch_start,
            inputs=[state_metas],
            outputs=[
                lbl_batch_progress,
                state_batch_index,
                txt_batch_disc,
                txt_batch_title,
                txt_batch_artist,
                txt_batch_current_url,
                txt_batch_path,
                txt_batch_query,
                txt_batch_album_hint,
                txt_batch_artist_hint,
                gallery_batch,
                state_results,
                img_batch_preview,
                txt_batch_selected_url,
                txt_batch_status,
                state_metas,
            ],
        )

        def on_batch_search(query: str, album_hint: str, artist_hint: str):
            items, results = do_search(query, album_hint, artist_hint)
            first_url = results[0].url_hires if results else ""
            first_img = first_url if first_url else None
            return (
                gr.update(value=items),
                results,
                first_img,
                first_url,
            )

        btn_batch_search.click(
            on_batch_search,
            inputs=[txt_batch_query, txt_batch_album_hint, txt_batch_artist_hint],
            outputs=[gallery_batch, state_results, img_batch_preview, txt_batch_selected_url],
        )

        txt_batch_query.submit(
            on_batch_search,
            inputs=[txt_batch_query, txt_batch_album_hint, txt_batch_artist_hint],
            outputs=[gallery_batch, state_results, img_batch_preview, txt_batch_selected_url],
        )

        def on_batch_gallery_select(results: list[ArtworkResult], evt: gr.SelectData):
            if not results or evt.index >= len(results):
                return "", None
            r = results[evt.index]
            return r.url_hires, r.url_hires

        gallery_batch.select(
            on_batch_gallery_select,
            inputs=[state_results],
            outputs=[txt_batch_selected_url, img_batch_preview],
        )

        def on_batch_preview_manual(url: str):
            url = url.strip()
            return url if url else None

        btn_batch_preview_manual.click(
            on_batch_preview_manual,
            inputs=[txt_batch_selected_url],
            outputs=[img_batch_preview],
        )

        def _advance_batch(metas: list[YamlMeta], current_index: int):
            """一括モードを次のファイルに進め、自動で検索まで実行する共通処理。"""
            new_metas = _reload_file_list()
            pending = _batch_pending(new_metas)
            total = len(pending)

            if total == 0:
                progress = "**完了: 全ファイルの処理が終わりました！**"
                return (
                    progress,
                    0,
                    "", "", "", "",
                    "", "", "", "",
                    gr.update(value=[], selected_index=None), [],
                    None, "",
                    "すべて処理済み",
                    new_metas,
                )

            next_idx = min(current_index, total - 1)
            meta = pending[next_idx]
            query = f"{meta.artist} {meta.release_title}".strip()
            progress = f"**{next_idx + 1} / {total} 件処理中** — {meta.disc_number}"

            # 自動検索
            items, results = do_search(query, "", "")
            first_url = results[0].url_hires if results else ""
            first_img = first_url if first_url else None

            return (
                progress,
                next_idx,
                meta.disc_number,
                meta.release_title,
                meta.artist,
                meta.artwork_url or "",
                str(meta.path),
                query,
                meta.release_title,
                meta.artist,
                gr.update(value=items),
                results,
                first_img,
                first_url,
                "",
                new_metas,
            )

        def on_batch_save_next(
            path_str: str,
            url: str,
            metas: list[YamlMeta],
            index: int,
        ):
            url = url.strip()
            if not url:
                return (gr.update(),) * 15 + (
                    "URLが未選択です。アートワークを選択してから保存してください。",
                    metas,
                )
            path = Path(path_str)
            try:
                write_artwork_url(path, url)
            except Exception as exc:  # noqa: BLE001
                return (gr.update(),) * 15 + (f"保存エラー: {exc}", metas)

            return _advance_batch(metas, index)

        btn_batch_save_next.click(
            on_batch_save_next,
            inputs=[txt_batch_path, txt_batch_selected_url, state_metas, state_batch_index],
            outputs=[
                lbl_batch_progress,
                state_batch_index,
                txt_batch_disc,
                txt_batch_title,
                txt_batch_artist,
                txt_batch_current_url,
                txt_batch_path,
                txt_batch_query,
                txt_batch_album_hint,
                txt_batch_artist_hint,
                gallery_batch,
                state_results,
                img_batch_preview,
                txt_batch_selected_url,
                txt_batch_status,
                state_metas,
            ],
        )

        def on_batch_skip(metas: list[YamlMeta], index: int):
            return _advance_batch(metas, index + 1)

        btn_batch_skip.click(
            on_batch_skip,
            inputs=[state_metas, state_batch_index],
            outputs=[
                lbl_batch_progress,
                state_batch_index,
                txt_batch_disc,
                txt_batch_title,
                txt_batch_artist,
                txt_batch_current_url,
                txt_batch_path,
                txt_batch_query,
                txt_batch_album_hint,
                txt_batch_artist_hint,
                gallery_batch,
                state_results,
                img_batch_preview,
                txt_batch_selected_url,
                txt_batch_status,
                state_metas,
            ],
        )

        # ===================================================================
        # 初期ロード
        # ===================================================================

        def on_load():
            metas = _reload_file_list()
            choices = _display_names(metas)
            return metas, gr.update(choices=choices, value=None)

        app.load(
            on_load,
            outputs=[state_metas, radio_files],
        )

    return app, _GALLERY_CSS, _GALLERY_JS


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdrdao-artwork",
        description="ブラウザ GUI でアルバムアートワークを検索・付与する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  uv run cdrdao-artwork
  uv run cdrdao-artwork --dir metadatas/
  uv run cdrdao-artwork --port 7861 --no-browser
""",
    )
    parser.add_argument(
        "--dir",
        metavar="PATH",
        type=Path,
        default=_DEFAULT_METADATAS_DIR,
        help=f"YAML ディレクトリ（デフォルト: {_DEFAULT_METADATAS_DIR}）",
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Gradio サーバーポート（デフォルト: {_DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="ブラウザを自動で開かない",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="Gradio の公開 URL を生成する（share=True）",
    )
    return parser


def main() -> None:
    import gradio as gr

    parser = _build_parser()
    args = parser.parse_args()

    if not args.dir.exists():
        print(f"エラー: ディレクトリが見つかりません: {args.dir}", file=sys.stderr)
        sys.exit(1)

    app, gallery_css, _unused_js = _build_app(args.dir)
    app.launch(
        server_port=args.port,
        inbrowser=not args.no_browser,
        share=args.share,
        show_error=True,
        theme=gr.themes.Soft(
            font=["system-ui", "sans-serif"],
            font_mono=["ui-monospace", "Consolas", "monospace"],
        ),
        css=gallery_css,
    )


if __name__ == "__main__":
    main()
