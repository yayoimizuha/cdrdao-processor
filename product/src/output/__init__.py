"""組み込みフォーマッターを import 時に登録する。"""

from src.output.json_formatter import JsonFormatter
from src.output.registry import register
from src.output.yaml_formatter import YamlFormatter

register(JsonFormatter())
register(YamlFormatter())
