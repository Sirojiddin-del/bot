#!/usr/bin/env python3
"""Bilim bazasini "davlat-xarid-agent" skillidan ``bilim/`` papkasiga ko'chiradi.

Foydalanish:
  python3 scripts/bilim_yukla.py /yol/davlat-xarid-agent        # skill papkasi
  python3 scripts/bilim_yukla.py /yol/davlat-xarid-agent.skill  # .skill (zip) fayli
  python3 scripts/bilim_yukla.py MANBA --maqsad boshqa/papka

Qayta ishga tushirilsa, bilim bazasi yangilanadi; siz to'ldirgan
``profil/tashkilot_profili.md`` saqlab qolinadi. ``bilim/`` .gitignore da —
skill mazmuni ochiq repoga tushmaydi.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_UNZIPPED = 200 * 1024 * 1024  # 200 MB — zip-bomba himoyasi
PROFIL = Path("profil") / "tashkilot_profili.md"
BELGI = ".manba.json"  # yuklash haqidagi qayd (profil shablonining xeshi)


class YuklashXatosi(Exception):
    pass


def find_skill_root(base: Path) -> Path:
    """SKILL.md joylashgan papkani topadi (eng yuqori darajadagisini)."""
    if (base / "SKILL.md").is_file():
        return base
    candidates = sorted(base.rglob("SKILL.md"), key=lambda p: len(p.parts))
    if not candidates:
        raise YuklashXatosi(f"{base} ichida SKILL.md topilmadi — bu skill papkasi emas.")
    return candidates[0].parent


def safe_extract(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNZIPPED:
            raise YuklashXatosi(f"arxiv juda katta ({total // (1024 * 1024)} MB).")
        dest_resolved = dest.resolve()
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                raise YuklashXatosi(f"arxivda xavfli yo'l bor: {info.filename}")
        zf.extractall(dest)


def xavfli_maqsad(target: Path) -> str | None:
    """Maqsad papkasi o'chirib qayta yoziladi — muhim papka bo'lsa, sababini qaytaradi."""
    if target == Path(target.anchor):
        return "bu disk ildizi"
    for nom, p in (("uy papkangiz", Path.home().resolve()), ("loyiha papkasi", ROOT),
                   ("joriy papka", Path.cwd().resolve())):
        if target == p or p.is_relative_to(target):
            return f"bu {nom} yoki uning yuqori papkasi"
    return None


def _ignore(directory: str, names: list[str]) -> list[str]:
    """Keraksiz fayllar va ramziy havolalar (symlink) ko'chirilmaydi.

    Symlink boshqa joydagi faylga (masalan, maxfiy kalitga) ishora qilishi mumkin —
    uning mazmuni bilim bazasiga tushib, agent orqali o'qilmasligi kerak.
    """
    skip = {n for n in names if n in {"__pycache__", ".DS_Store", ".git"} or n.endswith(".pyc")}
    skip |= {n for n in names if os.path.islink(os.path.join(directory, n))}
    return sorted(skip)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profil_tahrirlangan(target: Path, yangi_shablon: Path) -> bool:
    """Mavjud bilim bazasidagi profil foydalanuvchi tomonidan to'ldirilganmi."""
    eski = target / PROFIL
    if not eski.is_file():
        return False
    try:
        shablon_xeshi = json.loads((target / BELGI).read_text(encoding="utf-8"))["profil_shablon_sha256"]
    except (OSError, ValueError, KeyError):
        # oldingi yuklash qaydi yo'q — yangi shablondan farq qilsa, ehtiyot uchun saqlaymiz
        return not yangi_shablon.is_file() or eski.read_bytes() != yangi_shablon.read_bytes()
    return _sha(eski) != shablon_xeshi


def yukla(manba: Path, maqsad: Path, majburan: bool = False) -> dict:
    """Skillni ``maqsad`` papkasiga ko'chiradi. Natija: {"fayllar", "klassifikator", "profil_saqlandi"}."""
    src = manba.expanduser().resolve()
    maqsad = maqsad.expanduser()
    if maqsad.is_symlink():
        raise YuklashXatosi(f"{maqsad} — ramziy havola (symlink); haqiqiy papka yo'lini bering.")
    target = maqsad.resolve()
    if not src.exists():
        raise YuklashXatosi(f"{src} topilmadi.")
    sabab = xavfli_maqsad(target)
    if sabab:
        raise YuklashXatosi(f"{target} ga yozib bo'lmaydi: {sabab}. Alohida papka ko'rsating (masalan, bilim/).")
    if target.exists():
        if not target.is_dir():
            raise YuklashXatosi(f"{target} papka emas.")
        bosh_emas = any(target.iterdir())
        if bosh_emas and not (target / "SKILL.md").is_file() and not majburan:
            raise YuklashXatosi(
                f"{target} papkasida boshqa fayllar bor va u bilim bazasiga o'xshamaydi (SKILL.md yo'q). "
                "Undagi hamma narsa o'chiriladi — ishonchingiz komil bo'lsa, --majburan bilan qayta ishga tushiring."
            )

    with tempfile.TemporaryDirectory() as tmp:
        if src.is_file():
            if not zipfile.is_zipfile(src):
                raise YuklashXatosi("fayl zip (.skill) arxivi emas.")
            safe_extract(src, Path(tmp))
            root = find_skill_root(Path(tmp))
        else:
            root = find_skill_root(src)
            if target == root or target.is_relative_to(root) or root.is_relative_to(target):
                raise YuklashXatosi("manba va maqsad papkalari ustma-ust tushmasin.")

        # Avval yonma-yon vaqtinchalik papkaga ko'chiramiz, keyin almashtiramiz:
        # ko'chirish yarmida uzilsa, eski bilim bazasi joyida qoladi.
        target.parent.mkdir(parents=True, exist_ok=True)
        yangi = target.parent / f".{target.name}.yangi-{os.getpid()}"
        eski = target.parent / f".{target.name}.eski-{os.getpid()}"
        for p in (yangi, eski):
            if p.exists():
                shutil.rmtree(p)
        try:
            shutil.copytree(root, yangi, ignore=_ignore)
            shablon = yangi / PROFIL
            profil_saqlandi = False
            shablon_xeshi = _sha(shablon) if shablon.is_file() else ""
            if target.is_dir() and _profil_tahrirlangan(target, shablon):
                if shablon.is_file():  # yangi shablon yonida turadi — yangi maydonlarni solishtirish uchun
                    shutil.copy2(shablon, shablon.with_name(shablon.name + ".yangi-shablon"))
                shablon.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target / PROFIL, shablon)
                profil_saqlandi = True
            (yangi / BELGI).write_text(json.dumps({
                "profil_shablon_sha256": shablon_xeshi,
                "yuklangan": dt.datetime.now().isoformat(timespec="seconds"),
            }), encoding="utf-8")

            if target.exists():
                target.rename(eski)
            try:
                yangi.rename(target)
            except OSError:
                if eski.exists():
                    eski.rename(target)
                raise
        finally:
            for p in (yangi, eski):
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)

    return {
        "fayllar": sum(1 for p in target.rglob("*") if p.is_file()),
        "klassifikator": (target / "data" / "klassifikator.json").is_file(),
        "profil_saqlandi": profil_saqlandi,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manba", help="skill papkasi yoki .skill/.zip fayli")
    ap.add_argument("--maqsad", default=str(ROOT / "bilim"), help="qayerga ko'chirish (sukut: bilim/)")
    ap.add_argument("--majburan", action="store_true",
                    help="maqsad papkasi bilim bazasiga o'xshamasa ham ustidan yozish")
    a = ap.parse_args()
    try:
        natija = yukla(Path(a.manba), Path(a.maqsad), a.majburan)
    except YuklashXatosi as e:
        sys.exit(f"Xato: {e}")
    target = Path(a.maqsad).expanduser().resolve()
    print(f"Tayyor: {natija['fayllar']} ta fayl {target} ga ko'chirildi.")
    print("Klassifikator bazasi: " + ("bor" if natija["klassifikator"] else "YO'Q — kod_qidir ishlamaydi"))
    if natija["profil_saqlandi"]:
        print(f"Siz to'ldirgan profil saqlab qolindi: {target / PROFIL}")
        print("  (skilldagi yangi shablon yonida: tashkilot_profili.md.yangi-shablon)")
    print("Server ishlab turgan bo'lsa, uni qayta ishga tushiring.")


if __name__ == "__main__":
    main()
