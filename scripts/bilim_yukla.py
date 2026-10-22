#!/usr/bin/env python3
"""Bilim bazasini "davlat-xarid-agent" skillidan ``bilim/`` papkasiga ko'chiradi.

Foydalanish:
  python3 scripts/bilim_yukla.py /yol/davlat-xarid-agent        # skill papkasi
  python3 scripts/bilim_yukla.py /yol/davlat-xarid-agent.skill  # .skill (zip) fayli
  python3 scripts/bilim_yukla.py MANBA --maqsad boshqa/papka

``bilim/`` .gitignore da — skill mazmuni ochiq repoga tushmaydi.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_UNZIPPED = 200 * 1024 * 1024  # 200 MB — zip-bomba himoyasi


def find_skill_root(base: Path) -> Path:
    """SKILL.md joylashgan papkani topadi (eng yuqori darajadagisini)."""
    if (base / "SKILL.md").is_file():
        return base
    candidates = sorted(base.rglob("SKILL.md"), key=lambda p: len(p.parts))
    if not candidates:
        sys.exit(f"Xato: {base} ichida SKILL.md topilmadi — bu skill papkasi emas.")
    return candidates[0].parent


def safe_extract(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNZIPPED:
            sys.exit(f"Xato: arxiv juda katta ({total // (1024 * 1024)} MB).")
        dest_resolved = dest.resolve()
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                sys.exit(f"Xato: arxivda xavfli yo'l bor: {info.filename}")
        zf.extractall(dest)


def copy_skill(src_root: Path, target: Path) -> None:
    if target.exists():
        if not target.is_dir():
            sys.exit(f"Xato: {target} papka emas.")
        shutil.rmtree(target)
    shutil.copytree(
        src_root,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manba", help="skill papkasi yoki .skill/.zip fayli")
    ap.add_argument("--maqsad", default=str(ROOT / "bilim"), help="qayerga ko'chirish (sukut: bilim/)")
    a = ap.parse_args()

    src = Path(a.manba).expanduser().resolve()
    target = Path(a.maqsad).expanduser().resolve()
    if not src.exists():
        sys.exit(f"Xato: {src} topilmadi.")

    if src.is_file():
        if not zipfile.is_zipfile(src):
            sys.exit("Xato: fayl zip (.skill) arxivi emas.")
        with tempfile.TemporaryDirectory() as tmp:
            safe_extract(src, Path(tmp))
            copy_skill(find_skill_root(Path(tmp)), target)
    else:
        root = find_skill_root(src)
        if target == root or target.is_relative_to(root) or root.is_relative_to(target):
            sys.exit("Xato: manba va maqsad papkalari ustma-ust tushmasin.")
        copy_skill(root, target)

    files = [p for p in target.rglob("*") if p.is_file()]
    has_kod = (target / "data" / "klassifikator.json").is_file()
    print(f"Tayyor: {len(files)} ta fayl {target} ga ko'chirildi.")
    print("Klassifikator bazasi: " + ("bor" if has_kod else "YO'Q — kod_qidir ishlamaydi"))


if __name__ == "__main__":
    main()
