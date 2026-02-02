#!/usr/bin/env python3
"""
TOCファイル内のFILE行のファイル名を、TOCファイル名に合わせて修正するスクリプト

使用方法:
    python fix_toc_filenames.py <directory>
    
例:
    python fix_toc_filenames.py ./tocs
"""

import argparse
import re
import sys
from pathlib import Path


def fix_toc_file(toc_path: Path, dry_run: bool = False) -> bool:
    """
    TOCファイル内のFILE行を修正する
    
    Args:
        toc_path: TOCファイルのパス
        dry_run: Trueの場合、実際には書き込まず変更内容を表示のみ
        
    Returns:
        変更があった場合True
    """
    # ファイル名（拡張子なし）を取得
    base_name = toc_path.stem
    new_bin_name = f"{base_name}.bin"
    
    # ファイルを読み込み
    content = toc_path.read_text(encoding='utf-8')
    
    # FILE行のパターン: FILE "xxx.bin" の形式
    pattern = r'(FILE\s+")([^"]+\.bin)(")'
    
    def replace_filename(match):
        prefix = match.group(1)
        old_filename = match.group(2)
        suffix = match.group(3)
        return f'{prefix}{new_bin_name}{suffix}'
    
    # 置換を実行
    new_content, count = re.subn(pattern, replace_filename, content)
    
    if count > 0 and content != new_content:
        print(f"[修正] {toc_path.name}")
        
        # 変更されたFILE行を表示
        old_matches = re.findall(r'FILE\s+"([^"]+\.bin)"', content)
        if old_matches:
            old_filename = old_matches[0]
            if old_filename != new_bin_name:
                print(f"  {old_filename} -> {new_bin_name}")
        
        if not dry_run:
            toc_path.write_text(new_content, encoding='utf-8')
            print(f"  ファイルを更新しました")
        else:
            print(f"  (dry-run: 実際には変更しません)")
        
        return True
    else:
        print(f"[スキップ] {toc_path.name} (変更不要)")
        return False


def process_directory(directory: Path, dry_run: bool = False) -> tuple[int, int]:
    """
    ディレクトリ内の全てのTOCファイルを処理する
    
    Args:
        directory: 処理対象のディレクトリ
        dry_run: Trueの場合、実際には書き込まず変更内容を表示のみ
        
    Returns:
        (処理したファイル数, 変更したファイル数)
    """
    toc_files = sorted(directory.glob("*.toc"))
    
    if not toc_files:
        print(f"警告: {directory} にTOCファイルが見つかりません")
        return 0, 0
    
    processed = 0
    modified = 0
    
    for toc_path in toc_files:
        processed += 1
        if fix_toc_file(toc_path, dry_run):
            modified += 1
    
    return processed, modified


def main():
    parser = argparse.ArgumentParser(
        description='TOCファイル内のFILE行のファイル名を、TOCファイル名に合わせて修正する'
    )
    parser.add_argument(
        'directory',
        type=str,
        help='処理対象のディレクトリパス'
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='実際には変更せず、変更内容を表示のみ'
    )
    
    args = parser.parse_args()
    
    directory = Path(args.directory)
    
    if not directory.exists():
        print(f"エラー: ディレクトリが存在しません: {directory}", file=sys.stderr)
        sys.exit(1)
    
    if not directory.is_dir():
        print(f"エラー: ディレクトリではありません: {directory}", file=sys.stderr)
        sys.exit(1)
    
    print(f"処理対象ディレクトリ: {directory.absolute()}")
    if args.dry_run:
        print("(dry-runモード: 実際には変更しません)\n")
    else:
        print()
    
    processed, modified = process_directory(directory, args.dry_run)
    
    print()
    print(f"完了: {processed}ファイル処理, {modified}ファイル修正")


if __name__ == '__main__':
    main()
