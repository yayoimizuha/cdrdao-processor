"""解決済み曲名とレジストリディスクの照合・スコアリング。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from src.models import DiscCandidate, DiscRecord, ResolvedTitle

# ---------------------------------------------------------------------------
# タイトル正規化 (PoC ``_norm_title`` と同等)
# ---------------------------------------------------------------------------

_TITLE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",  # 左シングルクォート
        "\u2019": "'",  # 右シングルクォート
        "\u201c": '"',  # 左ダブルクォート
        "\u201d": '"',  # 右ダブルクォート
        "\u301c": "~",  # 波ダッシュ
        "\uff5e": "~",  # 全角チルダ
    }
)


def normalize_title(s: str | None) -> str:
    """タイトルを比較用に正規化する。

    NFKC 正規化、casefold、一般的な表記ゆれの統一、
    区切り文字 / 制御文字 / 句読点 / 記号の除去。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).strip().casefold()
    s = s.translate(_TITLE_TRANSLATION)
    out: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat[0] in {"Z", "C", "P", "S"}:
            continue
        out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


@dataclass
class ScorerConfig:
    """候補フィルタリング・自動選択の閾値設定。"""

    # フィルタリング
    absolute_floor: float = 0.05
    min_score_if_confident: float = 0.20
    min_relative_to_best: float = 0.60
    top_k: int = 10

    # 自動選択
    auto_pick_min_score: float = 0.70
    auto_pick_min_gap: float = 0.15


_DEFAULT_CFG = ScorerConfig()


# ---------------------------------------------------------------------------
# スコアリング
# ---------------------------------------------------------------------------


def _score_disc(
    resolved: list[ResolvedTitle],
    disc: DiscRecord,
) -> float:
    """1 つのディスクを解決済みタイトルリストと照合してスコアを算出する。

    位置ベース比較 + 包含判定フォールバック + 長さペナルティ。
    """
    observed = [r.title for r in resolved]
    candidate = [t.title for t in disc.tracks]

    if not observed:
        return 0.0

    n = min(len(observed), len(candidate))
    if n == 0:
        return 0.0

    total = 0.0
    comparable = 0

    for i in range(n):
        o = normalize_title(observed[i])
        c = normalize_title(candidate[i])
        if not o or not c:
            continue
        comparable += 1
        if o == c:
            total += 1.0
        elif o in c or c in o:
            total += 0.5

    if comparable == 0:
        return 0.0

    length_penalty = 1.0 - (
        abs(len(observed) - len(candidate)) / max(len(observed), len(candidate))
    )
    length_penalty = max(0.0, length_penalty)

    return (total / comparable) * length_penalty


def score_candidates(
    resolved: list[ResolvedTitle],
    registry: list[DiscRecord],
    cfg: ScorerConfig = _DEFAULT_CFG,
) -> list[DiscCandidate]:
    """*registry* 全件をスコアリングし、フィルタ済み候補をスコア降順で返す。"""
    scored: list[DiscCandidate] = []

    for disc in registry:
        s = _score_disc(resolved, disc)
        if s <= 0:
            continue
        scored.append(
            DiscCandidate(
                disc_number=disc.disc_number,
                release_title=disc.release_title,
                artist=disc.artist,
                release_date=disc.release_date,
                score=s,
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    if not scored:
        return []

    best = scored[0].score
    threshold = max(cfg.absolute_floor, best * cfg.min_relative_to_best)
    if best >= cfg.min_score_if_confident:
        threshold = max(threshold, cfg.min_score_if_confident)

    filtered = [c for c in scored if c.score >= threshold]
    return filtered[: cfg.top_k]


def should_auto_pick(
    candidates: list[DiscCandidate],
    cfg: ScorerConfig = _DEFAULT_CFG,
) -> bool:
    """1 位候補が十分に高スコアであれば ``True`` を返す。"""
    if not candidates:
        return False
    if candidates[0].score < cfg.auto_pick_min_score:
        return False
    if len(candidates) == 1:
        return True
    return (candidates[0].score - candidates[1].score) >= cfg.auto_pick_min_gap
