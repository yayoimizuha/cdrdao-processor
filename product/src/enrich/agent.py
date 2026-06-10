"""pydantic-ai ベースの音楽メタデータエンリッチメントエージェント。

LLM に search_web / fetch_web_page / ask_user ツールを渡し、
lyricist / composer / arranger / singer の補完値を TrackCredits 型で返す。

対象トラック:
    lyricist / composer / arranger / singer が **全て null** のトラックのみ。

キャッシュ:
    cache/enrich_cache.json にキー "<disc_number>:<track_number>:<title>" で永続保存。
    ヒット時は LLM を呼ばずキャッシュから返す。

コールバック:
    TUI から渡される 2 つのコールバックで UI と連携する。
    - on_tool_call(kind, detail)  : ツール呼び出しを UI に通知
    - ask_user_fn(question) -> str: ユーザーへの質問（ブロッキング）
    - confirm_fn(credits) -> bool : 承認を求める（ブロッキング）
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.enrich.fetch import fetch_page
from src.enrich.search import search

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_PATH: Final[Path] = (
    Path(__file__).parents[2] / "cache" / "enrich_cache.json"
)

CREDIT_FIELDS: Final[tuple[str, ...]] = ("lyricist", "composer", "arranger", "singer")

_SYSTEM_PROMPT = """\
あなたは日本の音楽情報に精通したアシスタントです。
与えられたCDトラックの lyricist（作詞者）/ composer（作曲者）/ \
arranger（編曲者）/ singer（歌手）を調べてください。

## 手順
1. まずユーザープロンプトに示された初期クエリで search_web を呼び出す。
2. スニペットだけでは不十分なら fetch_web_page で該当ページを取得する。
3. 情報が見つからない、または確信が持てない場合は ask_user を呼んでユーザーに質問する。
4. 回答を確認できたら、根拠にした URL を sources リストに必ず含めて返す。

## ルール
- 確認できなかった項目は null を返す（推測で埋めない）。
- Instrumental トラック（タイトルに "Instrumental" / "inst." を含む）は \
lyricist を null にする。
- sources には実際に情報を確認した URL のみを含める。
- 返却は指定された JSON スキーマに厳密に従うこと。
"""


# ---------------------------------------------------------------------------
# 出力型
# ---------------------------------------------------------------------------


class TrackCredits(BaseModel):
    """LLM エージェントが返す補完クレジット情報。"""

    lyricist: str | None = None
    composer: str | None = None
    arranger: str | None = None
    singer: str | None = None
    sources: list[str] = []


# ---------------------------------------------------------------------------
# エンリッチキャッシュ
# ---------------------------------------------------------------------------


class EnrichCache:
    """エンリッチ結果の永続 JSON キャッシュ。

    キー: "<disc_number>:<track_number>:<title>"
    値 : { lyricist, composer, arranger, singer, sources }
    """

    _MISSING = object()

    def __init__(self, path: Path = _DEFAULT_CACHE_PATH) -> None:
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def make_key(disc_number: str, track_number: int, title: str) -> str:
        return f"{disc_number}:{track_number}:{title}"

    def get(self, key: str) -> dict[str, Any] | object:
        if key in self._data:
            return self._data[key]
        return self._MISSING

    def set(self, key: str, credits: TrackCredits) -> None:
        self._data[key] = credits.model_dump()
        self._save()

    @property
    def path(self) -> Path:
        return self._path


# ---------------------------------------------------------------------------
# エージェント設定
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """エージェント生成に必要な設定値。"""

    model: str
    base_url: str | None = None
    api_key: str | None = None
    max_retries: int = 2


# ---------------------------------------------------------------------------
# コールバック型
# ---------------------------------------------------------------------------

# (kind: str, detail: str, result: str) -> None
#   kind  : "search" | "fetch" | "ask_user"
#   detail: 呼び出し引数（クエリ or URL or 質問文）
#   result: ツールの戻り値テキスト
OnToolCall = Callable[[str, str, str], None]

# (question: str) -> str  ブロッキング
AskUserFn = Callable[[str], str]

# (credits: TrackCredits, disc_number: str, track_number: int, title: str) -> str | None  ブロッキング
#   None  : 承認
#   ""    : 理由なし却下（再試行なし）
#   "<str>": コメント付き却下 → そのコメントをプロンプトに追記して再試行
ConfirmFn = Callable[["TrackCredits", str, int, str], "str | None"]


# ---------------------------------------------------------------------------
# エージェント構築
# ---------------------------------------------------------------------------


def build_agent(
    config: AgentConfig,
    on_tool_call: OnToolCall | None = None,
    ask_user_fn: AskUserFn | None = None,
) -> Agent[None, TrackCredits]:
    """設定とコールバックから pydantic-ai エージェントを構築して返す。

    Args:
        config: LLM 接続設定。
        on_tool_call: ツール呼び出し時に UI へ通知するコールバック。
        ask_user_fn: ask_user ツールが呼ばれたときにユーザー入力を得るコールバック。
                     None の場合は標準入力にフォールバック。
    """
    openai_model = OpenAIModel(
        config.model,
        provider=OpenAIProvider(
            base_url=config.base_url,
            api_key=config.api_key,
        ),
    )

    agent: Agent[None, TrackCredits] = Agent(
        openai_model,
        output_type=TrackCredits,
        system_prompt=_SYSTEM_PROMPT,
        retries=config.max_retries,
    )

    @agent.tool_plain
    def search_web(query: str) -> str:
        """Web を検索し、関連するテキストスニペットを返す。

        Args:
            query: 検索クエリ文字列（日本語可）。
        """
        results = search(query)
        result_text = "\n\n---\n\n".join(results) if results else "（検索結果なし）"
        if on_tool_call:
            on_tool_call("search", query, result_text)
        return result_text

    @agent.tool_plain
    def fetch_web_page(url: str) -> str:
        """指定 URL のページ内容を Markdown テキストで返す。

        JavaScript レンダリング後の DOM を取得する。

        Args:
            url: 取得する URL（https:// で始まること）。
        """
        result_text = fetch_page(url)
        if on_tool_call:
            on_tool_call("fetch", url, result_text)
        return result_text

    @agent.tool_plain
    def ask_user(question: str) -> str:
        """調査で解決できない場合にユーザーへ質問する。

        Args:
            question: ユーザーへの質問文。
        """
        if ask_user_fn:
            answer = ask_user_fn(question)
        else:
            # フォールバック: 標準入力
            print(f"\n[質問] {question}")
            answer = input("回答> ").strip()
        if on_tool_call:
            on_tool_call("ask_user", question, answer)
        return answer

    return agent


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def needs_enrich(track: dict[str, Any]) -> bool:
    """lyricist / composer / arranger / singer が全て null なら True を返す。"""
    return all(track.get(f) is None for f in CREDIT_FIELDS)


def enrich_track(
    agent: Agent[None, TrackCredits],
    cache: EnrichCache,
    disc_number: str,
    track_number: int,
    title: str,
    isrc: str | None,
    artist: str | None,
    release_title: str | None,
    yaml_context: str,
    confirm_fn: ConfirmFn | None = None,
) -> TrackCredits:
    """1 トラック分のクレジット情報を補完して返す。

    キャッシュヒット時は LLM を呼ばない。
    承認が得られなかった場合は None を返す。
    コメント付き却下の場合はコメントをプロンプトに追記して再試行する。

    Args:
        agent: build_agent() で生成したエージェント。
        cache: EnrichCache インスタンス。
        disc_number: 品番（例: "EPCE-7933"）。
        track_number: トラック番号（1-indexed）。
        title: 曲名。
        isrc: ISRC コード（あれば）。
        artist: アーティスト名。
        release_title: リリースタイトル。
        yaml_context: YAML ファイル全体のテキスト（コンテキストとして付与）。
        confirm_fn: 補完結果の承認を求めるコールバック。None なら自動承認。
                    戻り値: None=承認 / ""=却下（再試行なし） / "<str>"=コメント付き再試行。

    Returns:
        TrackCredits（承認済み）、または却下時は None。
    """
    cache_key = EnrichCache.make_key(disc_number, track_number, title)
    cached = cache.get(cache_key)
    if cached is not EnrichCache._MISSING:
        logger.debug("cache hit: %s", cache_key)
        return TrackCredits.model_validate(cached)

    # 初期検索クエリ
    initial_query = " ".join(filter(None, [release_title, artist, title]))
    isrc_str = f"ISRC: {isrc}" if isrc else "ISRC: 不明"

    base_prompt = (
        f"以下の楽曲のクレジット情報を調べてください。\n"
        f"まず「{initial_query}」で検索してください。\n\n"
        f"品番: {disc_number}\n"
        f"リリースタイトル: {release_title or '不明'}\n"
        f"アーティスト: {artist or '不明'}\n"
        f"トラック番号: {track_number}\n"
        f"曲名: {title}\n"
        f"{isrc_str}\n\n"
        f"## このCDのYAML全体（参考情報）\n"
        f"```yaml\n{yaml_context}\n```\n"
    )

    logger.info(
        "LLM enrich: [%s] track %d %r (attempt 1)",
        disc_number, track_number, title,
    )

    result = agent.run_sync(base_prompt)
    attempt = 1

    while True:
        credits = result.output

        # confirm_fn がない場合は自動承認
        if confirm_fn is None:
            cache.set(cache_key, credits)
            return credits

        feedback = confirm_fn(credits, disc_number, track_number, title)

        if feedback is None:
            # 承認
            cache.set(cache_key, credits)
            return credits

        if feedback == "":
            # 理由なし却下（再試行しない）
            logger.info("rejected by user: %s track %d", disc_number, track_number)
            return None

        # コメント付き却下 → 会話履歴を引き継いでコメントをユーザーターンとして追加し再試行
        # message_history を渡すことで LLM は前回の検索・取得内容を把握したまま再調査できる
        attempt += 1
        logger.info(
            "retry with user comment: %s track %d (attempt %d): %r",
            disc_number, track_number, attempt, feedback,
        )
        retry_prompt = (
            f"上記の提案に対してユーザーからコメントがありました。\n"
            f"コメント: {feedback}\n\n"
            f"このコメントを踏まえて再調査し、修正した情報を返してください。"
        )
        result = agent.run_sync(
            retry_prompt,
            message_history=result.all_messages(),
        )
