"""YAML 出力フォーマッター。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class YamlFormatter:
    format_name: str = "yaml"
    file_extension: str = ".yaml"

    def write(self, payload: dict[str, Any], dest: Path) -> None:
        dest.write_text(
            yaml.dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
