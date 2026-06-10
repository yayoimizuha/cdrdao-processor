"""JSON 出力フォーマッター。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonFormatter:
    format_name: str = "json"
    file_extension: str = ".json"

    def write(self, payload: dict[str, Any], dest: Path) -> None:
        dest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
