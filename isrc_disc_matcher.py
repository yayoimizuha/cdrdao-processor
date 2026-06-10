r"""ISRC -> Track titles -> Disc Number matcher.

Workflow:
1) Read ISRCs from either:
   - the currently inserted audio CD (via python-discid), or
   - a cdrdao image (.toc + .bin) via toc_parser.py.
2) For each ISRC, use yt-dlp to resolve a track title (fail-fast).
3) Compare the resulting ordered title list against merged_release_registry.xlsx
   and identify the most likely Disc Number.

If multiple candidates are plausible, the script interactively asks the user.

Usage:
  uv run python isrc_disc_matcher.py
  uv run python isrc_disc_matcher.py --toc .\tocs\EPCE-7845.toc
  uv run python isrc_disc_matcher.py --toc .\tocs\EPCE-7845.toc --isrc-only
  uv run python isrc_disc_matcher.py --bin D:\\path\\EPCE-7845.bin  (infers .toc)

Notes:
- In --toc/--bin mode, discid is not imported.
- Title matching is heuristic and uses normalization.
"""

from __future__ import annotations

import argparse
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    import pandas as pd


_HERE = Path(__file__).resolve().parent
REGISTRY_XLSX = _HERE / "merged_release_registry.xlsx"

DEFAULT_TOP_K = 10

# Candidate filtering: keep only sufficiently plausible matches.
ABSOLUTE_SCORE_FLOOR = 0.05
MIN_SCORE_IF_CONFIDENT = 0.20
MIN_RELATIVE_TO_BEST = 0.60

AUTO_PICK_MIN_SCORE = 0.70
AUTO_PICK_MIN_GAP = 0.15

_TITLE_TRANSLATION = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "〜": "~",
        "～": "~",
    }
)


def _norm_title(s: str) -> str:
    """Normalize titles for matching.

    Policy:
    - Unicode NFKC
    - case-insensitive (casefold)
    - remove whitespace, punctuation, symbols, controls

    This avoids regex and relies mostly on unicodedata.
    """

    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s).strip().casefold()

    # A few common variants that NFKC doesn't fully unify in practice
    s = s.translate(_TITLE_TRANSLATION)

    out: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        # Z*: separators (incl. spaces), C*: control/format/surrogate/private-use
        if cat[0] in {"Z", "C"}:
            continue
        # P*: punctuation, S*: symbols
        if cat[0] in {"P", "S"}:
            continue
        out.append(ch)

    return "".join(out)


def _yt_dlp_extract_info(query: str, *, timeout_sec: int = 30) -> Mapping[str, Any]:
    """Extract info via yt-dlp's Python API (no subprocess).

    Fail-fast: propagate errors so problems are visible.
    """

    from yt_dlp import YoutubeDL

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": timeout_sec,
    }

    with YoutubeDL(ydl_opts) as ydl:
        data = ydl.extract_info(query, download=False)

    if not isinstance(data, dict):
        raise TypeError("yt-dlp returned non-dict extract_info result")

    return data


def _extract_title_from_yt_dlp_json(data: Mapping[str, Any]) -> str | None:
    """Try multiple fields for a reasonable track title."""

    for key in ("track", "title", "alt_title"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    music = data.get("music")
    if isinstance(music, dict):
        val = music.get("track") or music.get("title")
        if isinstance(val, str) and val.strip():
            return val.strip()

    return None


def resolve_title_by_isrc(isrc: str) -> str | None:
    """Resolve a track title using yt-dlp (fail-fast if yt-dlp errors)."""

    query = f"ytsearch1:{isrc}"
    data = _yt_dlp_extract_info(query)

    entries = data.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                title = _extract_title_from_yt_dlp_json(entry)
                if title:
                    return title
        return None

    return _extract_title_from_yt_dlp_json(data)


def _ensure_discid_on_path() -> None:
    from os import environ
    from os.path import pathsep

    dll_dir = str(_HERE / "discid_dll")
    environ["PATH"] = dll_dir + pathsep + environ.get("PATH", "")


def read_cd_isrcs() -> list[str]:
    _ensure_discid_on_path()
    import discid  # type: ignore

    device = discid.get_default_device()
    info = discid.read(device, features=["mcn", "isrc"])

    isrcs: list[str] = []
    for t in info.tracks:
        if getattr(t, "isrc", None):
            isrcs.append(t.isrc)
        else:
            isrcs.append("")

    return isrcs


def read_toc_isrcs(
    toc_path: str | Path,
    *,
    encoding: str = "utf-8",
    bin_path: str | Path | None = None,
    bin_dir: str | Path | None = None,
) -> tuple[list[str], Path | None]:
    from toc_parser import parse_file

    toc_path = Path(toc_path)
    toc = parse_file(toc_path, encoding=encoding)

    isrcs = [(t.isrc or "") for t in toc.tracks]

    resolved_bin: Path | None = None
    if bin_path is not None:
        resolved_bin = Path(bin_path)
    elif toc.bin_file:
        name = Path(toc.bin_file)
        if name.is_absolute():
            resolved_bin = name
        else:
            base = Path(bin_dir) if bin_dir is not None else toc_path.parent
            resolved_bin = base / name

    return isrcs, resolved_bin


@dataclass(frozen=True)
class DiscCandidate:
    disc_number: str
    release_title: str | None
    artist: str | None
    release_date: str | None
    score: float


def _build_disc_tracklists(df: "pd.DataFrame") -> dict[str, list[str]]:
    """Return mapping: Disc Number -> list of Track Title (ordered)."""

    df2 = df[["Disc Number", "Track Order", "Track Title"]].dropna(
        subset=["Disc Number", "Track Order", "Track Title"]
    )
    df2 = df2.assign(**{"Track Order": df2["Track Order"].astype(int)})
    df2 = df2.sort_values(by=["Disc Number", "Track Order"], ascending=[True, True])

    # Keep Disc Number as string keys for stable printing/comparison
    return (
        df2.groupby("Disc Number", sort=False)["Track Title"]
        .apply(lambda s: [str(x) for x in s.tolist()])
        .rename(index=lambda x: str(x))
        .to_dict()
    )


def _score_match(observed: list[str], candidate: list[str]) -> float:
    """Score based on ordered title matches.

    Simple heuristic:
    - Compare by position (1:1) for the min length.
    - Exact normalized matches count as 1.0
    - Partial containment (either direction) counts as 0.5

    Empty/unknown observed titles are excluded from the denominator.
    """

    if not observed:
        return 0.0

    n = min(len(observed), len(candidate))
    if n == 0:
        return 0.0

    total = 0.0
    comparable = 0
    for i in range(n):
        o = _norm_title(observed[i])
        c = _norm_title(candidate[i])
        if not o or not c:
            continue
        comparable += 1
        if o == c:
            total += 1.0
        elif o in c or c in o:
            total += 0.5

    if comparable == 0:
        return 0.0

    # Penalize different track counts slightly (still use raw lengths)
    length_penalty = 1.0 - (abs(len(observed) - len(candidate)) / max(len(observed), len(candidate)))
    length_penalty = max(0.0, length_penalty)

    return (total / comparable) * length_penalty


def find_disc_candidates(
    observed_titles: list[str],
    registry_df: "pd.DataFrame",
    *,
    top_k: int = DEFAULT_TOP_K,
    min_relative_to_best: float = MIN_RELATIVE_TO_BEST,
    min_score_if_confident: float = MIN_SCORE_IF_CONFIDENT,
    absolute_floor: float = ABSOLUTE_SCORE_FLOOR,
) -> list[DiscCandidate]:
    disc_to_titles = _build_disc_tracklists(registry_df)

    # For better display, keep some disc metadata from first row per disc
    meta = (
        registry_df.dropna(subset=["Disc Number"])
        .sort_values(by=["Disc Number", "Track Order"], ascending=[True, True])
        .groupby("Disc Number")
        .head(1)
        .set_index("Disc Number")
    )

    scored: list[DiscCandidate] = []
    for disc_number, candidate_titles in disc_to_titles.items():
        score = _score_match(observed_titles, candidate_titles)
        if score <= 0:
            continue

        row = meta.loc[disc_number] if disc_number in meta.index else None
        scored.append(
            DiscCandidate(
                disc_number=disc_number,
                release_title=(None if row is None else str(row.get("Release Title", "") or "") or None),
                artist=(None if row is None else str(row.get("Artist", "") or "") or None),
                release_date=(None if row is None else str(row.get("Release Date", "") or "") or None),
                score=score,
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    if not scored:
        return []

    best = scored[0].score
    # If best is already "confident", enforce a minimum absolute score too.
    threshold = max(absolute_floor, best * min_relative_to_best)
    if best >= min_score_if_confident:
        threshold = max(threshold, min_score_if_confident)

    filtered = [c for c in scored if c.score >= threshold]
    return filtered[:top_k]


def _prompt_choice(cands: list[DiscCandidate]) -> DiscCandidate | None:
    if not cands:
        return None

    print("\nMultiple candidates found. Select one:")
    for idx, c in enumerate(cands, start=1):
        meta = " / ".join([x for x in [c.release_title, c.artist, c.release_date] if x])
        print(f"  [{idx}] {c.disc_number}  score={c.score:.3f}  {meta}")

    while True:
        s = input(f"Enter 1-{len(cands)} (or blank to cancel): ").strip()
        if not s:
            return None
        if s.isdigit():
            i = int(s)
            if 1 <= i <= len(cands):
                return cands[i - 1]
        print("Invalid selection.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ISRC -> Track titles -> Disc Number matcher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--toc", type=Path, help="Path to a cdrdao .toc file")
    parser.add_argument(
        "--bin",
        type=Path,
        help="Path to a cdrdao .bin file (used to infer the corresponding .toc)",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        help="Directory containing the .bin referenced by the .toc (if relative)",
    )
    parser.add_argument("--toc-encoding", default="utf-8", help="Encoding for reading .toc")
    parser.add_argument(
        "--isrc-only",
        action="store_true",
        help="Print ISRCs from the input source and exit",
    )
    args = parser.parse_args(argv)

    toc_path: Path | None = args.toc
    bin_override: Path | None = args.bin

    if toc_path is None and bin_override is not None:
        # .toc carries ISRC metadata; .bin presence is optional for this script.
        toc_path = bin_override.with_suffix(".toc")

    # 1) Read ISRCs
    if toc_path is not None:
        if not toc_path.exists():
            raise FileNotFoundError(toc_path)
        isrcs, resolved_bin = read_toc_isrcs(
            toc_path,
            encoding=args.toc_encoding,
            bin_path=bin_override,
            bin_dir=args.bin_dir,
        )
        print(f"Input: TOC {toc_path}")
        if resolved_bin is not None:
            status = "found" if resolved_bin.exists() else "missing"
            print(f"BIN: {resolved_bin} ({status})")
        label = "TOC"
    else:
        isrcs = read_cd_isrcs()
        label = "CD"

    if not any(isrcs):
        print(f"No ISRCs found from {label}.")
        return 2

    print(f"ISRCs from {label} (blank means missing):")
    for i, isrc in enumerate(isrcs, start=1):
        print(f"  {i:02d}: {isrc}")

    if args.isrc_only:
        return 0

    # 2) Resolve titles via yt-dlp
    observed_titles: list[str] = []
    print("\nResolving track titles via yt-dlp...")
    for i, isrc in enumerate(isrcs, start=1):
        if not isrc:
            observed_titles.append("")
            print(f"  {i:02d}: (no ISRC) -> (skipped)")
            continue
        title = resolve_title_by_isrc(isrc)
        observed_titles.append(title or "")
        print(f"  {i:02d}: {isrc} -> {title}")

    if not any(observed_titles):
        print("Could not resolve any titles via yt-dlp.")
        return 3

    # 3) Load registry and score
    import pandas as pd

    df = pd.read_excel(REGISTRY_XLSX, engine="openpyxl")
    cands = find_disc_candidates(observed_titles, df)

    if not cands:
        print("No matching Disc Number found in registry.")
        return 4

    # If top is clearly better, auto-pick
    if len(cands) == 1 or (
        len(cands) >= 2
        and (cands[0].score - cands[1].score) >= AUTO_PICK_MIN_GAP
        and cands[0].score >= AUTO_PICK_MIN_SCORE
    ):
        best = cands[0]
        print(f"\nBest match: {best.disc_number} (score={best.score:.3f})")
        return 0

    chosen = _prompt_choice(cands)
    if chosen is None:
        print("Cancelled.")
        return 5

    print(f"\nSelected: {chosen.disc_number} (score={chosen.score:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
