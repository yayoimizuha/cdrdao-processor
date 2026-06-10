"""cdrdao-enrich エントリポイント。

生成済み YAML の lyricist / composer / arranger / singer を
AI エージェント（LLM + Web 検索）で自動補完する。
処理中は Textual ベースの Chat TUI を表示する。

使用例:
    python -m src.cli.enrich_cmd --dir metadatas/
    python -m src.cli.enrich_cmd --file metadatas/UFCW-1166.yaml --dry-run
    python -m src.cli.enrich_cmd --dir metadatas/ --model gpt-4o
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.enrich.agent import AgentConfig, EnrichCache
from src.enrich.tui import run_enrich_tui

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o"
_DEFAULT_METADATAS_DIR = Path(__file__).parents[2] / "metadatas"
_DEFAULT_CACHE_PATH = Path(__file__).parents[2] / "cache" / "enrich_cache.json"


# ---------------------------------------------------------------------------
# CLI 引数
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdrdao-enrich",
        description="生成済み YAML の音楽クレジット情報を AI エージェントで自動補完する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python -m src.cli.enrich_cmd --dir metadatas/
  python -m src.cli.enrich_cmd --file metadatas/UFCW-1166.yaml --dry-run
  python -m src.cli.enrich_cmd --dir metadatas/ --model gpt-4o-mini
  python -m src.cli.enrich_cmd --dir metadatas/ \\
      --base-url http://localhost:11434/v1 --model qwen2.5:7b
""",
    )

    # 対象指定（どちらか一方）
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--dir",
        metavar="PATH",
        type=Path,
        default=_DEFAULT_METADATAS_DIR,
        help=f"対象ディレクトリ（デフォルト: {_DEFAULT_METADATAS_DIR}）",
    )
    target.add_argument(
        "--file",
        metavar="PATH",
        type=Path,
        help="単一 YAML ファイルを指定",
    )

    # 動作モード
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ファイルを変更せず差分を表示するだけ",
    )

    # LLM 設定
    parser.add_argument(
        "--model",
        metavar="NAME",
        default=None,
        help="使用するモデル名（環境変数 ENRICH_MODEL / デフォルト: gpt-4o）",
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        default=None,
        help="OpenAI 互換 API の base URL（環境変数 ENRICH_BASE_URL）",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        default=None,
        help="API キー（環境変数 OPENAI_API_KEY）",
    )
    parser.add_argument(
        "--max-retries",
        metavar="N",
        type=int,
        default=2,
        help="LLM 呼び出し失敗時の最大リトライ回数（デフォルト: 2）",
    )

    # キャッシュ
    parser.add_argument(
        "--cache",
        metavar="PATH",
        type=Path,
        default=_DEFAULT_CACHE_PATH,
        help=f"キャッシュファイルパス（デフォルト: {_DEFAULT_CACHE_PATH}）",
    )

    # ログ
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="詳細ログを stderr に出力する",
    )

    return parser


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    # product/.env を読み込む（既に設定済みの環境変数は上書きしない）
    load_dotenv(Path(__file__).parents[2] / ".env", override=False)

    parser = _build_parser()
    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # 接続情報解決（CLI引数 > 環境変数 > デフォルト）
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    base_url = args.base_url or os.environ.get("ENRICH_BASE_URL")
    model = args.model or os.environ.get("ENRICH_MODEL") or _DEFAULT_MODEL

    if not api_key and not base_url:
        parser.error(
            "API キーが未設定です。"
            " --api-key または環境変数 OPENAI_API_KEY を設定してください。"
        )

    config = AgentConfig(
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_retries=args.max_retries,
    )
    cache = EnrichCache(path=args.cache)

    # 対象ファイルリスト収集
    if args.file:
        if not args.file.exists():
            parser.error(f"ファイルが見つかりません: {args.file}")
        yaml_paths = [args.file]
    else:
        if not args.dir.exists():
            parser.error(f"ディレクトリが見つかりません: {args.dir}")
        yaml_paths = sorted(args.dir.glob("*.yaml")) + sorted(args.dir.glob("*.yml"))
        if not yaml_paths:
            print(f"YAML ファイルが見つかりません: {args.dir}", file=sys.stderr)
            sys.exit(1)

    # TUI 起動
    run_enrich_tui(yaml_paths, config, cache, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
