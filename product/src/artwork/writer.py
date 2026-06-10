"""YAML ファイルへの artwork_url フィールド読み書き。

YAML の disc セクションに artwork_url を追加・更新する。
既存の構造・コメント・フィールド順は可能な限り維持する。

インターフェース:
    read_artwork_url(path: Path) -> str | None
        YAML から現在の artwork_url を読み取る。

    write_artwork_url(path: Path, url: str | None) -> None
        YAML の disc.artwork_url を更新して保存する。
        url が None の場合はフィールドを削除する。

    list_yaml_files(directory: Path) -> list[YamlMeta]
        ディレクトリ内の全 YAML を走査してメタ情報のリストを返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def read_artwork_url(path: Path) -> str | None:
    """YAML ファイルから artwork_url を読み取る。

    Args:
        path: 対象 YAML ファイルパス。

    Returns:
        artwork_url 文字列。未設定の場合は None。
    """
    data = _load_yaml(path)
    disc: dict[str, Any] = data.get("disc", {})
    value = disc.get("artwork_url")
    return str(value) if value else None


def write_artwork_url(path: Path, url: str | None) -> None:
    """YAML ファイルの disc.artwork_url を更新して保存する。

    disc セクションが存在しない場合は新規作成する。
    url が None の場合はフィールドをキーごと削除する。

    Args:
        path: 対象 YAML ファイルパス。
        url: 保存するアートワーク URL。None でフィールド削除。
    """
    data = _load_yaml(path)

    if "disc" not in data:
        data["disc"] = {}

    if url is None:
        data["disc"].pop("artwork_url", None)
    else:
        data["disc"]["artwork_url"] = url

    _save_yaml(path, data)


# ---------------------------------------------------------------------------
# ファイル一覧
# ---------------------------------------------------------------------------


@dataclass
class YamlMeta:
    """YAML ファイルのメタ情報（UI の一覧表示用）。"""

    path: Path
    """YAML ファイルパス"""

    disc_number: str
    """品番（YAML の disc.disc_number）"""

    release_title: str
    """リリースタイトル（YAML の disc.release_title）"""

    artist: str
    """アーティスト名（YAML の disc.artist）"""

    artwork_url: str | None
    """現在設定されているアートワーク URL（未設定は None）"""

    @property
    def has_artwork(self) -> bool:
        return bool(self.artwork_url)

    @property
    def display_name(self) -> str:
        status = "✓" if self.has_artwork else "✗"
        return f"{status} {self.disc_number} — {self.release_title}"


def list_yaml_files(directory: Path) -> list[YamlMeta]:
    """ディレクトリ内の全 YAML ファイルを走査してメタ情報リストを返す。

    ファイル名（品番）の昇順でソートして返す。

    Args:
        directory: 走査するディレクトリパス。

    Returns:
        YamlMeta のリスト。YAML の読み取りに失敗したファイルはスキップ。
    """
    results: list[YamlMeta] = []

    paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))

    for path in paths:
        try:
            data = _load_yaml(path)
        except Exception:  # noqa: BLE001
            continue

        disc = data.get("disc", {})
        results.append(
            YamlMeta(
                path=path,
                disc_number=str(disc.get("disc_number", path.stem)),
                release_title=str(disc.get("release_title", "")),
                artist=str(disc.get("artist", "")),
                artwork_url=disc.get("artwork_url") or None,
            )
        )

    return results
