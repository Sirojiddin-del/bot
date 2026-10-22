"""Bilim bazasi: skill fayllarini o'qish, matn qidirish va klassifikator qidiruvi.

Bilim bazasi (``bilim/`` papkasi) repoga kirmaydi — uni ``scripts/bilim_yukla.py``
"davlat-xarid-agent" skill papkasidan yoki ``.skill`` faylidan ko'chiradi.
"""
from __future__ import annotations

import json
import re
from functools import cached_property
from pathlib import Path

APOSTROPHES = "'`‘’ʻʼ´"
CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ь": "", "ы": "i", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}
_CYR_RE = re.compile(r"[а-яёўқғҳ]")
_SPLIT_RE = re.compile(r"[\s,;/()\[\]\-.:\"«»*]+")

# Bilim bazasidagi o'qiladigan matn fayllari
TEXT_SUFFIXES = {".md", ".txt", ".json", ".html"}
# Kod bilan emas, alohida vosita orqali ishlatiladigan katta fayllar
BLOCKED_FILES = {"data/klassifikator.json"}
SEARCH_DIRS = ("laws", "references", "templates", "profil")
MAX_READ_CHARS = 60_000


def norm(text: str) -> str:
    """Kichik harf, apostroflarni birxillashtirish, ortiqcha bo'shliqlarni olib tashlash."""
    text = str(text).lower()
    for a in APOSTROPHES:
        text = text.replace(a, "'")
    return re.sub(r"\s+", " ", text).strip()


def to_latin(text: str) -> str:
    """Kirill (o'zbek va rus) harflarini lotinga o'giradi — qidiruv uchun yagona ko'rinish."""
    return "".join(CYR2LAT.get(ch, ch) for ch in text)


def has_cyrillic(text: str) -> bool:
    return bool(_CYR_RE.search(text))


def words(text: str) -> list[str]:
    return [w for w in _SPLIT_RE.split(text) if w]


class BilimError(Exception):
    """Foydalanuvchiga (modelga) ko'rsatiladigan xato."""


class Bilim:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # ---------- umumiy ----------
    def mavjud(self) -> bool:
        return (self.root / "SKILL.md").is_file()

    def skill_matni(self) -> str:
        return (self.root / "SKILL.md").read_text(encoding="utf-8")

    def _safe_path(self, rel: str) -> Path:
        rel = (rel or "").strip().lstrip("/")
        if not rel:
            raise BilimError("Fayl yo'li bo'sh.")
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root):
            raise BilimError("Bilim bazasidan tashqaridagi fayllarni o'qib bo'lmaydi.")
        return target

    def _text_files(self) -> list[Path]:
        files = [
            p for p in self.root.rglob("*")
            if p.is_file() and p.suffix in TEXT_SUFFIXES
            and p.relative_to(self.root).as_posix() not in BLOCKED_FILES
        ]
        return sorted(files, key=lambda p: p.relative_to(self.root).as_posix())

    # ---------- ro'yxat ----------
    def royxat(self) -> str:
        lines = []
        for p in self._text_files():
            rel = p.relative_to(self.root).as_posix()
            title = ""
            if p.suffix == ".md":
                with p.open(encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("#"):
                            title = line.lstrip("#").strip()
                            break
            size_kb = max(1, p.stat().st_size // 1024)
            lines.append(f"{rel}  ({size_kb} KB){'  — ' + title if title else ''}")
        lines.append("data/klassifikator.json  — faqat kod_qidir vositasi orqali")
        return "\n".join(lines)

    # ---------- o'qish ----------
    def oqish(self, yol: str, boshlanish: int = 1, qatorlar: int = 400) -> str:
        target = self._safe_path(yol)
        rel = target.relative_to(self.root).as_posix()
        if rel in BLOCKED_FILES:
            raise BilimError("Klassifikator bazasini o'qimang — kod_qidir vositasidan foydalaning.")
        if not target.is_file():
            raise BilimError(f"Fayl topilmadi: {rel}. bilim_royxati vositasi bilan yo'lni tekshiring.")
        if target.suffix not in TEXT_SUFFIXES:
            raise BilimError(f"Bu fayl turi o'qilmaydi: {target.suffix}")
        all_lines = target.read_text(encoding="utf-8").splitlines()
        total = len(all_lines)
        start = max(1, int(boshlanish))
        count = max(1, min(int(qatorlar), 2000))
        if start > total:
            raise BilimError(f"{rel} faylida {total} qator bor; boshlanish {start} juda katta.")
        out, chars, last = [], 0, start - 1
        for i in range(start - 1, min(total, start - 1 + count)):
            line = f"{i + 1}\t{all_lines[i]}"
            if chars + len(line) > MAX_READ_CHARS and out:
                break
            out.append(line)
            chars += len(line) + 1
            last = i + 1
        header = f"{rel} — {start}–{last}-qatorlar (jami {total})"
        footer = ""
        if last < total:
            footer = f"\n[davomi bor: boshlanish={last + 1} bilan o'qing]"
        return header + "\n" + "\n".join(out) + footer

    # ---------- matn qidirish ----------
    def qidirish(self, soz: str, papka: str | None = None, n: int = 30) -> str:
        query = to_latin(norm(soz))
        if len(query) < 2:
            raise BilimError("Qidiruv so'zi kamida 2 harf bo'lsin.")
        if papka and papka not in SEARCH_DIRS:
            raise BilimError(f"papka quyidagilardan biri bo'lsin: {', '.join(SEARCH_DIRS)}")
        dirs = [papka] if papka else list(SEARCH_DIRS)
        files = [p for p in self._text_files() if p.relative_to(self.root).parts[0] in dirs]
        if not papka:
            files.insert(0, self.root / "SKILL.md")
        query_words = words(query)

        def scan(match_all_words: bool) -> list[tuple[str, int, str]]:
            hits = []
            for p in files:
                if not p.is_file():
                    continue
                rel = p.relative_to(self.root).as_posix()
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    hay = to_latin(norm(line))
                    ok = all(w in hay for w in query_words) if match_all_words else query in hay
                    if ok:
                        hits.append((rel, i, line.strip()))
            return hits

        hits = scan(match_all_words=False)
        mode = "ibora"
        if not hits and len(query_words) > 1:
            hits = scan(match_all_words=True)
            mode = "barcha so'zlar bir qatorda"
        if not hits:
            return (f"\"{soz}\" topilmadi. Sinonim, qisqaroq o'zak yoki boshqa tildagi "
                    "(rus/kirill) shaklini sinab ko'ring.")
        n = max(1, min(int(n), 100))
        per_file: dict[str, int] = {}
        for rel, _, _ in hits:
            per_file[rel] = per_file.get(rel, 0) + 1
        lines = [f"\"{soz}\" — {len(hits)} ta moslik ({mode}). Fayllar bo'yicha: "
                 + ", ".join(f"{k} ({v})" for k, v in per_file.items())]
        for rel, i, text in hits[:n]:
            if len(text) > 300:
                text = text[:300] + "…"
            lines.append(f"{rel}:{i}: {text}")
        if len(hits) > n:
            lines.append(f"[yana {len(hits) - n} ta — aniqroq so'z yoki papka bilan toraytiring]")
        return "\n".join(lines)

    # ---------- klassifikator ----------
    @cached_property
    def klassifikator(self) -> "Klassifikator":
        path = self.root / "data" / "klassifikator.json"
        if not path.is_file():
            raise BilimError("Klassifikator bazasi (data/klassifikator.json) topilmadi.")
        return Klassifikator(path)


class Klassifikator:
    """Mahsulot (tovar, ish, xizmat) klassifikatori bo'yicha oflayn qidiruv."""

    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.holat = data.get("fetched", "?")
        self.manba = data.get("source", "?")
        self.items: list[tuple[str, str, str]] = [(r[0], r[1], r[2]) for r in data["items"]]
        # Har bir pozitsiya uchun qidiruv maydonlari: o'zbekcha (lotin), ruscha (kirill), ruscha (lotin)
        self._fields = []
        for _, uz, ru in self.items:
            uz_n = to_latin(norm(uz))
            ru_n = norm(ru)
            ru_lat = to_latin(ru_n)
            self._fields.append((uz_n, words(uz_n), ru_n, words(ru_n), ru_lat, words(ru_lat)))

    def __len__(self) -> int:
        return len(self.items)

    @staticmethod
    def _forms(tok: str) -> list[str]:
        # o'zbekcha undosh almashuvi: sochiq -> sochig'i, ko'ylak -> ko'ylagi
        forms = [tok]
        if tok.endswith("q"):
            forms.append(tok[:-1] + "g'")
        if tok.endswith("k"):
            forms.append(tok[:-1] + "g")
        return forms

    def _token_score(self, tok: str, ws: list[str], text: str) -> float:
        forms = self._forms(tok)
        if any(w == f for w in ws for f in forms):
            return 1.3
        if any(w.startswith(f) for w in ws for f in forms):
            return 1.1
        if any(f in text for f in forms):
            return 0.7
        for cut in (1, 2):
            if len(tok) - cut >= 4 and any(w.startswith(tok[:-cut]) for w in ws):
                return 0.6
        return 0.0

    def _score(self, query: str, ws: list[str], text: str, head: str) -> float:
        toks = [t for t in words(query) if len(t) >= 2]
        if not toks or not text:
            return 0.0
        scores = [self._token_score(t, ws, text) for t in toks]
        found = sum(1 for s in scores if s > 0)
        if found == 0 or found / len(toks) < 0.5:
            return 0.0
        s = sum(scores) / len(toks) * 100
        if query in text:
            s += 40
            if text.startswith(query):
                s += 10
        # Bosh so'z — pozitsiyaning o'zi shu narsa ekanini bildiradi
        # (o'zbekchada oxirgi so'z: "maishiy konditsioner"; ruschada birinchi: "кондиционер бытовой")
        if head and any(head == f for t in toks for f in self._forms(t)):
            s += 12
        s -= 4 * max(0, len(ws) - len(toks))
        s -= len(text) / 40
        return s

    def qidir(self, soz: str, n: int = 8) -> list[tuple[float, tuple[str, str, str]]]:
        q = norm(soz)
        cyr = has_cyrillic(q)
        q_lat = to_latin(q)
        results = []
        for item, (uz_n, uz_w, ru_n, ru_w, ru_lat, ru_lat_w) in zip(self.items, self._fields):
            uz_head = uz_w[-1] if uz_w else ""
            if cyr:
                best = max(
                    self._score(q, ru_w, ru_n, ru_w[0] if ru_w else ""),
                    self._score(q_lat, uz_w, uz_n, uz_head),
                )
            else:
                best = max(
                    self._score(q, uz_w, uz_n, uz_head),
                    self._score(q, ru_lat_w, ru_lat, ru_lat_w[0] if ru_lat_w else "") - 5,
                )
            if best > 0:
                results.append((best, item))
        results.sort(key=lambda r: -r[0])
        return results[: max(1, n)]

    def prefiks(self, kod: str) -> list[tuple[str, str, str]]:
        kod = kod.strip()
        return [it for it in self.items if it[0].startswith(kod)]
