"""Bilim bazasi: skill fayllarini o'qish, matn qidirish va klassifikator qidiruvi.

Bilim bazasi (``bilim/`` papkasi) repoga kirmaydi — uni ``scripts/bilim_yukla.py``
"davlat-xarid-agent" skill papkasidan yoki ``.skill`` faylidan ko'chiradi.
"""
from __future__ import annotations

import json
import re
import threading
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
MAX_LINE_CHARS = 20_000
# Klassifikator qidiruvi har so'z uchun butun bazani ko'rib chiqadi — so'rov hajmini cheklaymiz
MAX_QUERY_CHARS = 100
MAX_QUERY_TOKENS = 8
UZ_VOWELS = ("a", "e", "i", "o", "u", "o'")
NO_MATCH = float("-inf")


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


def _yashirin(rel: Path) -> bool:
    """Nuqta bilan boshlanadigan (xizmat) fayl va papkalar vositalarga ko'rinmaydi."""
    return any(part.startswith(".") for part in rel.parts)


class BilimError(Exception):
    """Foydalanuvchiga (modelga) ko'rsatiladigan xato."""


class Bilim:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._lines: dict[Path, tuple[int, int, list[str], list[str]]] = {}
        self._klassifikator: Klassifikator | None = None
        self._klassifikator_lock = threading.Lock()

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
            if p.suffix in TEXT_SUFFIXES and p.is_file()
            # ramziy havolalar (symlink) orqali bilim bazasidan tashqariga chiqilmasin
            and not p.is_symlink() and p.resolve().is_relative_to(self.root)
            and p.relative_to(self.root).as_posix() not in BLOCKED_FILES
            and not _yashirin(p.relative_to(self.root))
        ]
        return sorted(files, key=lambda p: p.relative_to(self.root).as_posix())

    def _file_lines(self, path: Path) -> tuple[list[str], list[str]]:
        """Fayl qatorlari va ularning qidiruv uchun me'yorlangan ko'rinishi (fayl o'zgarmaguncha keshda)."""
        st = path.stat()
        cached = self._lines.get(path)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2], cached[3]
        lines = path.read_text(encoding="utf-8").splitlines()
        normed = [to_latin(norm(line)) for line in lines]
        self._lines[path] = (st.st_mtime_ns, st.st_size, lines, normed)
        return lines, normed

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
        if not target.is_file() or _yashirin(Path(rel)):
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
            text = all_lines[i]
            if len(text) > MAX_LINE_CHARS:
                text = f"{text[:MAX_LINE_CHARS]}… [qator qisqartirildi: jami {len(text)} belgi]"
            line = f"{i + 1}\t{text}"
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
        if len(query) > 200:
            raise BilimError("Qidiruv iborasi juda uzun — 200 belgidan qisqa so'z yoki ibora bering.")
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
                lines, normed = self._file_lines(p)
                for i, hay in enumerate(normed):
                    ok = all(w in hay for w in query_words) if match_all_words else query in hay
                    if ok:
                        hits.append((rel, i + 1, lines[i].strip()))
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
    @property
    def klassifikator(self) -> "Klassifikator":
        with self._klassifikator_lock:  # bir vaqtda ikki oqim yuklamasin (3 MB JSON)
            if self._klassifikator is None:
                path = self.root / "data" / "klassifikator.json"
                if not path.is_file():
                    raise BilimError("Klassifikator bazasi (data/klassifikator.json) topilmadi.")
                self._klassifikator = Klassifikator(path)
            return self._klassifikator


class _Sorov:
    """Bir tildagi me'yorlangan so'rov: so'zlar va ularning shakllari oldindan hisoblanadi."""

    def __init__(self, text: str, uzbek: bool):
        self.text = text
        self.uzbek = uzbek
        # har so'z uchun: (so'z, aniq shakllar, turlangan shakllar, old qo'shimcha sifatida qidiriladigan o'zaklar)
        self.toks: list[tuple[str, frozenset[str], frozenset[str], tuple[str, ...]]] = []
        for tok in [t for t in words(text) if len(t) >= 2][:MAX_QUERY_TOKENS]:
            exact = {tok}
            inflected: set[str] = set()
            stems = {tok}
            if uzbek:
                # so'rovdagi egalik/ko'plik qo'shimchasini olib tashlaymiz: qog'ozi -> qog'oz, xizmatlari -> xizmat
                for suffix in ("lari", "lar", "si", "i"):
                    if tok.endswith(suffix) and len(tok) - len(suffix) >= 3:
                        stems.add(tok[: -len(suffix)])
                        break
                for stem in list(stems):
                    # undosh almashuvi: sochiq -> sochig'i, ko'ylak -> ko'ylagi
                    if stem.endswith("q"):
                        stems.add(stem[:-1] + "g'")
                    if stem.endswith("k"):
                        stems.add(stem[:-1] + "g")
                for stem in stems:
                    # egalik va ko'plik: sut -> suti, energiya -> energiyasi, xizmat -> xizmatlar(i)
                    inflected.add(stem)
                    inflected.add(stem + ("si" if stem.endswith(UZ_VOWELS) else "i"))
                    inflected.update((stem + "lar", stem + "lari"))
                inflected -= exact
            prefixes = tuple(sorted(st for st in stems if st == tok or len(st) >= 4))
            self.toks.append((tok, frozenset(exact), frozenset(inflected), prefixes))


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
    def _token_score(tok: str, exact: frozenset[str], inflected: frozenset[str], prefixes: tuple[str, ...],
                     ws: list[str], text: str) -> float:
        if any(w in exact for w in ws):
            return 1.3
        if inflected and any(w in inflected for w in ws):
            return 1.25
        if any(w.startswith(prefixes) for w in ws):
            return 1.1
        if len(tok) >= 4:  # qisqa so'zlar boshqa so'z ichida tasodifan uchraydi ("ai" — "benzocaine")
            if any(pre in text for pre in prefixes):
                return 0.7
            for cut in (1, 2):
                if len(tok) - cut >= 5 and any(w.startswith(tok[:-cut]) for w in ws):
                    return 0.6
        return 0.0

    def _score(self, q: _Sorov, ws: list[str], text: str, min_found: float) -> float:
        """Moslik bali; mos kelmasa — NO_MATCH (ball manfiy ham bo'lishi mumkin)."""
        if not q.toks or not ws:
            return NO_MATCH
        scores = [self._token_score(*t, ws, text) for t in q.toks]
        found = sum(1 for s in scores if s > 0)
        if found == 0 or found < min_found * len(scores):
            return NO_MATCH
        s = sum(scores) / len(scores) * 100
        s -= 35 * (len(scores) - found)  # hamma so'z topilgan pozitsiyalar yuqorida tursin
        # Bosh so'z pozitsiyaning o'zi shu narsa ekanini bildiradi. O'zbekchada u oxirida keladi
        # ("ichimlik suti" — sut; "sut zardobi" — zardob), ruschada boshida ("молоко питьевое").
        head = ws[-1] if q.uzbek else ws[0]
        if any(head in exact for _, exact, _, _ in q.toks):
            s += 25
        elif any(head in inflected for _, _, inflected, _ in q.toks):
            s += 20
        if len(scores) > 1 and q.text in text:  # ibora butunligicha uchraydi
            s += 40
            if text.endswith(q.text) if q.uzbek else text.startswith(q.text):
                s += 10
        s -= 4 * max(0, len(ws) - len(scores))
        s -= len(text) / 40
        return s

    def _scan(self, text: str, min_found: float) -> list[tuple[float, tuple[str, str, str]]]:
        results = []
        if has_cyrillic(text):
            q_ru, q_uz = _Sorov(text, uzbek=False), _Sorov(to_latin(text), uzbek=True)
            for item, (uz_n, uz_w, ru_n, ru_w, _, _) in zip(self.items, self._fields):
                best = max(self._score(q_ru, ru_w, ru_n, min_found), self._score(q_uz, uz_w, uz_n, min_found))
                if best > NO_MATCH:
                    results.append((best, item))
        else:
            q_uz, q_ru = _Sorov(text, uzbek=True), _Sorov(text, uzbek=False)
            for item, (uz_n, uz_w, _, _, ru_lat, ru_lat_w) in zip(self.items, self._fields):
                # lotincha yozilgan ruscha so'z ("printer", "toner") ruscha nom bo'yicha ham topiladi
                best = max(self._score(q_uz, uz_w, uz_n, min_found),
                           self._score(q_ru, ru_lat_w, ru_lat, min_found) - 5)
                if best > NO_MATCH:
                    results.append((best, item))
        return results

    def qidir(self, soz: str, n: int = 8) -> list[tuple[float, tuple[str, str, str]]]:
        text = norm(soz)[:MAX_QUERY_CHARS]
        # avval so'zlarning kamida yarmi topilgan pozitsiyalar; hech narsa chiqmasa — istalgan bittasi
        results = self._scan(text, 0.5) or self._scan(text, 0.0)
        results.sort(key=lambda r: -r[0])
        return results[: max(1, n)]

    def prefiks(self, kod: str) -> list[tuple[str, str, str]]:
        kod = kod.strip()
        return [it for it in self.items if it[0].startswith(kod)]
