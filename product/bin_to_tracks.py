"""bin_to_tracks.py — YAML メタデータを元に .bin を分割して ALAC (.m4a) にエンコードする。

使い方:
    uv run python bin_to_tracks.py metadatas/EPCE-7267.yaml --out /path/to/output
    uv run python bin_to_tracks.py metadatas/

出力ファイル名:
    {disc_number}/{track_number:02d} - {title}.m4a

依存:
    ffmpeg (外部コマンド) — ALAC エンコードに使用
    mutagen              — MP4 タグ書き込みに使用

タグ方針:
    Apple Music / iTunes で認識される標準 MP4 タグは標準キーで書き込む。
    それ以外のフィールド (arranger, catalog, disc_type, release_type 等) は
    ----:com.apple.iTunes:EXTENDED_TAGS に JSON 文字列としてまとめて格納する。
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows の CP932 端末で日本語・中国語を print するとクラッシュするため UTF-8 に固定
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import yaml
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

# CD-DA 定数
BYTES_PER_FRAME = 2352          # 44100 Hz × 16bit × 2ch / 75fps
SAMPLE_RATE     = 44100
CHANNELS        = 2
BITS_PER_SAMPLE = 16


# ---------------------------------------------------------------------------
# RAW PCM → WAV バイト列 (ffmpeg への中間フォーマット)
# ---------------------------------------------------------------------------

def _build_wav_bytes(pcm_be: bytes) -> bytes:
    """CD-DA raw (big-endian) PCM を little-endian に swap して WAV を構築する。
    ffmpeg への stdin 入力用。
    """
    arr = bytearray(pcm_be)
    for i in range(0, len(arr) - 1, 2):
        arr[i], arr[i + 1] = arr[i + 1], arr[i]
    pcm_le = bytes(arr)

    data_size   = len(pcm_le)
    byte_rate   = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8
    block_align = CHANNELS * BITS_PER_SAMPLE // 8

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH",
        16, 1, CHANNELS, SAMPLE_RATE,
        byte_rate, block_align, BITS_PER_SAMPLE,
    ))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_le)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ALAC エンコード
# ---------------------------------------------------------------------------

def encode_alac(pcm_be: bytes, dest: Path) -> None:
    """CD-DA raw PCM を ffmpeg で ALAC (.m4a) にエンコードして dest に書き出す。"""
    wav = _build_wav_bytes(pcm_be)

    # まず stdin から読む形式を試みる
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "wav", "-i", "pipe:0",
                "-c:a", "alac",
                str(dest),
            ],
            input=wav,
            capture_output=True,
        )
        if proc.returncode == 0:
            return
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg が見つかりません。ffmpeg をインストールして PATH に追加してください。"
        )

    # stdin が使えない環境向け: 一時 WAV ファイル経由
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        tmp_path.write_bytes(wav)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(tmp_path),
                "-c:a", "alac",
                str(dest),
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg が失敗しました (rc={proc.returncode}):\n"
                + proc.stderr.decode(errors="replace")
            )
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# MP4 タグ付け
# ---------------------------------------------------------------------------

def _release_date(release_date: str | None) -> str | None:
    """'YYYY-MM-DD HH:MM:SS' などから日付部分 'YYYY-MM-DD' を取り出す。"""
    if not release_date:
        return None
    return release_date.strip()[:10]


def tag_mp4(
    path: Path,
    track: dict,
    disc: dict,
    track_num: int,
    total_tracks: int,
    artwork: bytes | None,
    artwork_mime: str | None,
    catalog: str | None,
    disc_index: int = 1,
    total_discs: int = 1,
) -> None:
    """ALAC (.m4a) ファイルに MP4 タグを書き込む。

    Apple Music で認識される標準キーは標準フォーマットで書き込む。
    それ以外のフィールドは ----:com.apple.iTunes:EXTENDED_TAGS に
    JSON としてまとめて格納する。
    """
    f = MP4(str(path))
    if f.tags is None:
        f.add_tags()
    f.tags.clear()

    def _set(key: str, val) -> None:
        if val is not None:
            f.tags[key] = [str(val)]

    # --- Apple Music / iTunes で表示される標準タグ ---
    _set("©nam", track.get("title"))
    _set("©ART", track.get("singer"))
    _release_title = disc.get("release_title")
    _disc_type     = disc.get("disc_type")
    _album_tag     = f"{_release_title} [{_disc_type}]" if _release_title and _disc_type else _release_title
    _set("©alb", _album_tag)
    _set("aART", disc.get("artist"))
    _set("©day", _release_date(disc.get("release_date")))
    _set("©wrt", track.get("composer"))   # ffprobe: "composer"
    _set("©gen", "Pop")

    # trkn / disk はタプルのリスト形式
    f.tags["trkn"] = [(track_num, total_tracks)]
    disc_num_str = disc.get("disc_number")  # 品番文字列 (例: "EPCE-7267")
    # disk タグは整数ペアのみ受け付けるため、品番はEXTENDED_TAGSに回す
    f.tags["disk"] = [(disc_index, total_discs)]

    # アートワーク
    if artwork:
        fmt = MP4Cover.FORMAT_JPEG if artwork_mime == "image/jpeg" else MP4Cover.FORMAT_PNG
        f.tags["covr"] = [MP4Cover(artwork, imageformat=fmt)]

    # --- EXTENDED_TAGS: Apple Music 非対応だが保持したいフィールド ---
    extended: dict = {}

    def _ext(key: str, val) -> None:
        if val is not None:
            extended[key] = str(val)

    _ext("LABEL",        disc.get("label"))
    _ext("DISC_NUMBER",  disc_num_str)          # 品番 (EPCE-7267 等)
    _ext("DISC_TYPE",    disc.get("disc_type"))
    _ext("RELEASE_TYPE", disc.get("release_type"))
    _ext("CATALOGNUMBER", catalog)
    _ext("ISRC",         track.get("isrc"))
    _ext("LYRICIST",     track.get("lyricist"))  # 作詞者 (©lyr は歌詞本文フィールドのため使用不可)
    _ext("ARRANGER",     track.get("arranger"))  # ©arg はコンテナに無視されるためここに格納

    if extended:
        f.tags["----:com.apple.iTunes:EXTENDED_TAGS"] = [
            MP4FreeForm(json.dumps(extended, ensure_ascii=False).encode("utf-8"))
        ]

    f.save()


# ---------------------------------------------------------------------------
# アートワーク取得
# ---------------------------------------------------------------------------

# URL → (bytes, mime) のプロセス内キャッシュ（同一URLを何度もダウンロードしない）
_artwork_cache: dict[str, tuple[bytes, str]] = {}

_ARTWORK_SIZE = "600x600-999"   # 埋め込み用サイズ（5000x5000 は過大）
_SIZE_RE = re.compile(r"\d+x\d+-\d+")


def _fetch_artwork(url: str) -> tuple[bytes, str]:
    import urllib.request
    # iTunes CDN URL のサイズ部分を差し替える
    url = _SIZE_RE.sub(_ARTWORK_SIZE, url)
    if url in _artwork_cache:
        return _artwork_cache[url]
    with urllib.request.urlopen(url, timeout=30) as resp:
        result = resp.read(), resp.headers.get_content_type()
    _artwork_cache[url] = result
    return result


# ---------------------------------------------------------------------------
# 1 YAML の処理
# ---------------------------------------------------------------------------

def _safe_filename(s: str) -> str:
    for ch in r'\/:*?"<>|':
        s = s.replace(ch, "_")
    return s.strip()


def process_yaml(yaml_path: Path, out_root: Path, *, embed_art: bool,
                 disc_index: int = 1, total_discs: int = 1) -> None:
    data   = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    source = data["source"]
    disc   = data["disc"]
    tracks = data["tracks"]

    raw     = Path(source["bin_path"]).read_bytes()
    catalog = source.get("catalog")

    artwork: bytes | None     = None
    artwork_mime: str | None  = None
    if embed_art:
        artwork_url = disc.get("artwork_url")
        if artwork_url:
            print(f"  アートワーク取得中...", end="", flush=True)
            artwork, artwork_mime = _fetch_artwork(artwork_url)
            print(" done")
        else:
            print("  artwork_url なし — スキップ")

    disc_dir = out_root / _safe_filename(disc["disc_number"])
    disc_dir.mkdir(parents=True, exist_ok=True)

    total = len(tracks)
    for track in tracks:
        num    = track["track_number"]
        title  = track.get("title") or f"track{num:02d}"
        offset = track["bin_offset"]

        start = offset["audio_offset_frames"] * BYTES_PER_FRAME
        end   = start + offset["audio_length_frames"] * BYTES_PER_FRAME
        pcm   = raw[start:end]

        fname = f"{num:02d} - {_safe_filename(title)}.m4a"
        dest  = disc_dir / fname
        print(f"  [{num:02d}/{total}] {fname}", end="", flush=True)

        encode_alac(pcm, dest)
        tag_mp4(dest, track, disc, num, total, artwork, artwork_mime, catalog,
                disc_index=disc_index, total_discs=total_discs)
        print(" ... done")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="YAML + .bin → ALAC (.m4a) 変換スクリプト",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "target",
        type=Path,
        help="処理対象の YAML ファイル、またはディレクトリ",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="出力先ディレクトリ（省略時は YAML と同じディレクトリ）",
    )
    parser.add_argument(
        "--no-art",
        action="store_true",
        help="アートワークを埋め込まない",
    )
    args = parser.parse_args()

    target: Path = args.target

    if target.is_dir():
        yaml_paths = sorted(target.glob("*.yaml")) + sorted(target.glob("*.yml"))
        if not yaml_paths:
            sys.exit(f"YAML ファイルが見つかりません: {target}")
    else:
        yaml_paths = [target]

    # 同一 release_title のYAMLをグループ化して disc_index / total_discs を決定する。
    # 品番末尾の連続した数字でソートし、グループ内の連番位置をディスク番号として使用する。
    # ただし release_type が "single" の場合はマルチディスク扱いせず常に 1/1 とする。
    def _parse_disc_meta(yp: Path) -> tuple[str, bool, int]:
        """(release_title, is_single, 品番末尾数字) を返す。"""
        data = yaml.safe_load(yp.read_text(encoding="utf-8"))
        disc_section = data.get("disc", {})
        title = disc_section.get("release_title") or ""
        is_single = disc_section.get("release_type", "").lower() == "single"
        disc_number = disc_section.get("disc_number", "")
        m = re.search(r"(\d+)$", disc_number)
        return title, is_single, (int(m.group(1)) if m else 0)

    # release_title → [(末尾数字, yaml_path), ...]
    # single の場合はグループキーを yaml_path 自身にして孤立させる（常に 1/1 扱い）
    groups: dict[str, list[tuple[int, Path]]] = collections.defaultdict(list)
    for yp in yaml_paths:
        title, is_single, trailing_num = _parse_disc_meta(yp)
        group_key = str(yp) if is_single else title
        groups[group_key].append((trailing_num, yp))
    for grp in groups.values():
        grp.sort(key=lambda x: x[0])

    # yaml_path → (disc_index, total_discs)
    disc_info: dict[Path, tuple[int, int]] = {}
    for grp in groups.values():
        total = len(grp)
        for idx, (_, yp) in enumerate(grp, start=1):
            disc_info[yp] = (idx, total)

    for yp in yaml_paths:
        out_root = args.out or yp.parent
        disc_index, total_discs = disc_info.get(yp, (1, 1))
        print(f"[{yp.name}] (disc {disc_index}/{total_discs})")
        process_yaml(yp, out_root, embed_art=not args.no_art,
                     disc_index=disc_index, total_discs=total_discs)


if __name__ == "__main__":
    main()
