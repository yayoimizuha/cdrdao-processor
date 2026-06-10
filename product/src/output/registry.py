"""フォーマッターレジストリ — フォーマット名からインスタンスを引く。"""

from __future__ import annotations

from src.output.base import OutputFormatter

_REGISTRY: dict[str, OutputFormatter] = {}


def register(formatter: OutputFormatter) -> None:
    """フォーマッターインスタンスを ``format_name`` で登録する。"""
    _REGISTRY[formatter.format_name] = formatter


def get(name: str) -> OutputFormatter:
    """*name* に対応するフォーマッターを返す。未登録なら :class:`KeyError`。"""
    return _REGISTRY[name]


def available() -> list[str]:
    """登録済みフォーマット名をソート順で返す。"""
    return sorted(_REGISTRY)
