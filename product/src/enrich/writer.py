"""YAML ファイルのエンリッチメント処理（読込・補完・保存）。

補完対象: lyricist / composer / arranger / singer が**全て null**のトラック。
--dry-run 指定時はファイルを変更せず差分のみ返す。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai import Agent

from src.enrich.agent import (
    AgentConfig,
    ConfirmFn,
    CREDIT_FIELDS,
    EnrichCache,
    TrackCredits,
    enrich_track,
    needs_enrich,
)
logger = logging.getLogger(__name__)

# TUI から渡されるログコールバック型
# (kind: ChatKind, text: str) -> None
OnLog = Callable[[Any, str], None]


# ---------------------------------------------------------------------------
# 結果型
# ---------------------------------------------------------------------------


@dataclass
class TrackDiff:
    """1 トラックの補完差分。"""

    track_number: int
    title: str
    before: dict[str, str | None]
    after: dict[str, str | None]

    @property
    def has_changes(self) -> bool:
        return self.before != self.after


@dataclass
class FileDiff:
    """1 ファイルの補完結果サマリー。"""

    path: Path
    track_diffs: list[TrackDiff]
    skipped: bool = False
    skip_reason: str = ""

    @property
    def changed_count(self) -> int:
        return sum(1 for d in self.track_diffs if d.has_changes)

    @property
    def has_changes(self) -> bool:
        return self.changed_count > 0


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _yaml_text(data: dict[str, Any]) -> str:
    """dict を YAML テキストに変換する（コンテキスト付与用）。"""
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# コア処理
# ---------------------------------------------------------------------------


def enrich_file(
    path: Path,
    agent: Agent[None, TrackCredits],
    cache: EnrichCache,
    *,
    confirm_fn: ConfirmFn | None = None,
    dry_run: bool = False,
    on_log: OnLog | None = None,
) -> FileDiff:
    """1 つの YAML ファイルを読み込み、対象トラックを補完して保存する。

    補完対象: lyricist / composer / arranger / singer が全て null のトラック。

    Args:
        path: 対象 YAML ファイルパス。
        agent: build_agent() で生成したエージェント。
        cache: EnrichCache インスタンス。
        confirm_fn: 補完結果の承認を求めるコールバック。None なら自動承認。
        dry_run: True のとき差分を返すだけでファイルを変更しない。
        on_log: TUI / CLI へのログ通知コールバック。

    Returns:
        FileDiff（変更内容の概要）。
    """
    data = _load_yaml(path)
    tracks: list[dict[str, Any]] = data.get("tracks", [])
    disc: dict[str, Any] = data.get("disc", {})

    disc_number: str = disc.get("disc_number", path.stem)
    artist: str | None = disc.get("artist")
    release_title: str | None = disc.get("release_title")

    # YAML 全体テキスト（コンテキスト用）
    yaml_context = _yaml_text(data)

    # 補完が必要なトラックが 1 件もなければスキップ
    if not any(needs_enrich(t) for t in tracks):
        return FileDiff(
            path=path,
            track_diffs=[],
            skipped=True,
            skip_reason="all credit fields are already filled",
        )

    track_diffs: list[TrackDiff] = []

    for track in tracks:
        track_number: int = track["track_number"]
        title: str = track.get("title") or ""
        isrc: str | None = track.get("isrc")

        before = {f: track.get(f) for f in CREDIT_FIELDS}

        if not needs_enrich(track):
            track_diffs.append(TrackDiff(track_number, title, before, before))
            continue

        try:
            credits = enrich_track(
                agent=agent,
                cache=cache,
                disc_number=disc_number,
                track_number=track_number,
                title=title,
                isrc=isrc,
                artist=artist,
                release_title=release_title,
                yaml_context=yaml_context,
                confirm_fn=confirm_fn,
            )
        except Exception:
            logger.exception(
                "enrich failed for %s track %d — skipping", path.name, track_number
            )
            track_diffs.append(TrackDiff(track_number, title, before, before))
            continue

        if credits is None:
            # ユーザーが却下
            logger.info("rejected by user: %s track %d", path.name, track_number)
            track_diffs.append(TrackDiff(track_number, title, before, before))
            continue

        credits_dict = credits.model_dump()
        after = {
            f: credits_dict.get(f) if credits_dict.get(f) is not None else before[f]
            for f in CREDIT_FIELDS
        }

        track_diffs.append(TrackDiff(track_number, title, before, after))

        if not dry_run:
            for f in CREDIT_FIELDS:
                new_val = credits_dict.get(f)
                if new_val is not None:
                    track[f] = new_val

    file_diff = FileDiff(path=path, track_diffs=track_diffs)

    if not dry_run and file_diff.has_changes:
        _save_yaml(path, data)
        logger.info("saved: %s (%d tracks updated)", path.name, file_diff.changed_count)

    return file_diff
