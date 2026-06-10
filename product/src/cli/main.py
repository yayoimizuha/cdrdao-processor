"""CLI エントリポイント — パイプライン全体のオーケストレーター。

使い方::

    uv run python -m src.cli.main --toc path/to/file.toc
    uv run python -m src.cli.main --dir path/to/tocs/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.models import DiscRecord, ResolvedTitle


def _find_disc_by_number(
    registry: list[DiscRecord], disc_number: str
) -> DiscRecord | None:
    return next((d for d in registry if d.disc_number == disc_number), None)


def _load_output_file(path: Path) -> dict:
    """出力済みファイル（JSON/YAML）を dict として読み込む。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        import json
        return json.loads(text)
    else:
        import yaml
        return yaml.safe_load(text)


def _process_single(
    toc_path: Path,
    registry: list[DiscRecord],
    *,
    output_dir: Path,
    format_name: str,
    isrc_only: bool,
    no_auto_pick: bool,
    toc_encoding: str,
    force: bool = False,
) -> int:
    """``.toc`` ファイル 1 つを処理する。成功時 0、スキップ/エラー時は非 0。"""
    import src.output  # noqa: F401  — 組み込みフォーマッター登録
    from src.output.registry import get as get_formatter

    formatter = get_formatter(format_name)

    # ── (1) 処理済みチェック ──
    if not force and output_dir.is_dir():
        toc_abs = str(toc_path.resolve())
        for existing in output_dir.glob(f"*{formatter.file_extension}"):
            data = _load_output_file(existing)
            if data.get("source", {}).get("toc_path") == toc_abs:
                print(f"  [SKIP] 出力済み: {existing.name}")
                return 0

    from src.toc.parser import parse_toc

    toc = parse_toc(toc_path, encoding=toc_encoding)

    # ── (2) ISRC → 曲名解決 ──
    from src.isrc.resolver import resolve_titles

    resolved = resolve_titles(toc.tracks)

    if isrc_only:
        for r in resolved:
            isrc_str = r.isrc or "(なし)"
            print(f"  {r.track_number:02d}: {isrc_str}")
        return 0

    # ── (2b) TUI: 未解決曲名の手入力 ──
    missing = [r for r in resolved if r.title is None]
    if missing:
        from src.cli.tui import input_missing_titles

        updated = input_missing_titles(resolved, toc_name=toc_path.name)
        if updated is not None:
            resolved = updated

    # ── (3) スコアリング ──
    from src.matching.scorer import ScorerConfig, score_candidates, should_auto_pick

    cfg = ScorerConfig()
    candidates = score_candidates(resolved, registry, cfg)

    disc: DiscRecord | None = None

    # ── (4a-pre) ファイル名とディスクナンバーが一致する候補があれば自動承認 ──
    toc_stem = toc_path.stem
    filename_match = next(
        (c for c in candidates if c.disc_number == toc_stem), None
    )
    if not no_auto_pick and filename_match is not None:
        print(f"  自動承認 (ファイル名一致): {filename_match.disc_number}")
        disc = _find_disc_by_number(registry, filename_match.disc_number)
    elif not candidates:
        # ── (4a) 候補なし → 手動入力 ──
        from src.cli.tui import input_manual_disc

        disc = input_manual_disc(resolved, toc_name=toc_path.name)
        if disc is None:
            print("  [SKIP]")
            return 1
    elif not no_auto_pick and should_auto_pick(candidates, cfg):
        # ── (4b) 自動選択 ──
        best = candidates[0]
        print(f"  自動選択: {best.disc_number} (score={best.score:.3f})")
        disc = _find_disc_by_number(registry, best.disc_number)
    else:
        # ── (4c) TUI 選択 ──
        from src.cli.tui import _MANUAL, input_manual_disc, select_candidate

        resolved_count = sum(1 for r in resolved if r.title)
        chosen = select_candidate(
            candidates,
            toc_name=toc_path.name,
            resolved_count=resolved_count,
            total_count=len(toc.tracks),
        )
        if chosen is None:
            print("  [SKIP]")
            return 1
        if chosen is _MANUAL:
            disc = input_manual_disc(resolved, toc_name=toc_path.name)
            if disc is None:
                print("  [SKIP]")
                return 1
        else:
            disc = _find_disc_by_number(registry, chosen.disc_number)

    if disc is None:
        print("  [SKIP] DiscRecord not found")
        return 1

    # ── (5) メタデータ出力 ──
    from src.output.base import build_payload

    payload = build_payload(toc, disc, resolved)
    dest = output_dir / f"{disc.disc_number}{formatter.file_extension}"
    formatter.write(payload, dest)
    print(f"  -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="cdrdao-processor: CD メタデータ付与パイプライン",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--toc", type=Path, help=".toc ファイルを指定 (単体モード)")
    group.add_argument(
        "--dir", type=Path, help="ディレクトリを指定 (バッチモード: 配下の .toc を全処理)"
    )

    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("merged_release_registry.xlsx"),
        help="レジストリ XLSX のパス",
    )
    parser.add_argument("--output", type=Path, default=None, help="出力先ディレクトリ")
    parser.add_argument("--format", default="json", dest="format_name", help="出力フォーマット")
    parser.add_argument(
        "--formats",
        action="store_true",
        help="利用可能なフォーマット一覧を表示して終了",
    )
    parser.add_argument(
        "--isrc-only",
        action="store_true",
        help="ISRC を表示して終了 (単体モードのみ)",
    )
    parser.add_argument(
        "--no-auto-pick",
        action="store_true",
        help="常に TUI 選択画面を表示する",
    )
    parser.add_argument(
        "--toc-encoding",
        default="utf-8",
        help="TOC ファイルのエンコーディング",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="出力済みファイルが存在しても再処理・上書きする",
    )

    args = parser.parse_args(argv)

    # --formats
    if args.formats:
        import src.output  # noqa: F401
        from src.output.registry import available

        for name in available():
            print(name)
        return 0

    if args.toc is None and args.dir is None:
        parser.error("--toc または --dir のいずれかが必要です")

    # レジストリ読込
    from src.registry.loader import load_registry

    registry = load_registry(args.registry)

    # .toc パス収集
    if args.toc:
        toc_paths = [args.toc]
        default_output = args.toc.parent
    else:
        assert args.dir is not None
        toc_paths = sorted(args.dir.glob("*.toc"))
        if not toc_paths:
            print(f"No .toc files found in {args.dir}")
            return 1
        default_output = args.dir

    output_dir = args.output or default_output
    output_dir.mkdir(parents=True, exist_ok=True)

    # 処理実行
    total = len(toc_paths)
    for idx, tp in enumerate(toc_paths, start=1):
        prefix = f"[{idx}/{total}] {tp.name}"
        print(prefix)
        _process_single(
            tp,
            registry,
            output_dir=output_dir,
            format_name=args.format_name,
            isrc_only=args.isrc_only,
            no_auto_pick=args.no_auto_pick,
            toc_encoding=args.toc_encoding,
            force=args.force,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
