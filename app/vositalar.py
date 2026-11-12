"""Agent vositalari (tools): ta'riflar va bajaruvchi funksiyalar.

Har bir vosita kirishni o'zi tekshiradi: oqimli (eager) kirishda API sxemani
tekshirmaydi, shuning uchun noto'g'ri kirish ``VositaXatosi`` bilan qaytariladi
va model uni ``is_error`` natija sifatida ko'radi.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from .bilim import Bilim, BilimError


MAX_SOZLAR = 10  # bitta kod_qidir chaqiruvidagi qidiruv so'zlari


class VositaXatosi(Exception):
    pass


def _require_str(args: dict, key: str, *, optional: bool = False) -> str | None:
    val = args.get(key)
    if val is None and optional:
        return None
    if not isinstance(val, str) or not val.strip():
        raise VositaXatosi(f"'{key}' matn bo'lishi kerak.")
    return val


def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    val = args.get(key, default)
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise VositaXatosi(f"'{key}' butun son bo'lishi kerak.")
    return max(lo, min(hi, int(val)))


def _num(val: Any, name: str) -> float:
    if isinstance(val, bool):
        raise VositaXatosi(f"'{name}' son bo'lishi kerak.")
    if isinstance(val, str):
        val = val.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        x = float(val)
    except (TypeError, ValueError):
        raise VositaXatosi(f"'{name}' son bo'lishi kerak.") from None
    if not math.isfinite(x):
        raise VositaXatosi(f"'{name}' chekli son bo'lishi kerak.")
    return x


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


# ---------------------------------------------------------------- vositalar

def kod_qidir(bilim: Bilim, args: dict) -> str:
    sozlar = args.get("sozlar") or []
    kod = _require_str(args, "kod", optional=True)
    if not isinstance(sozlar, list) or not all(isinstance(s, str) for s in sozlar):
        raise VositaXatosi("'sozlar' matnlar ro'yxati bo'lishi kerak.")
    sozlar = [s.strip() for s in sozlar if s.strip()]
    if not sozlar and not kod:
        raise VositaXatosi("'sozlar' yoki 'kod' dan kamida bittasini bering.")
    n = _int(args, "n", 8, 1, 30)
    k = bilim.klassifikator
    out = [f"Klassifikator: {len(k)} pozitsiya ({k.manba}, holati {k.holat}). Format: kod | o'zbekcha | ruscha"]
    if len(sozlar) > MAX_SOZLAR:
        out.append(f"[{len(sozlar)} ta so'zdan faqat birinchi {MAX_SOZLAR} tasi qidirildi]")
        sozlar = sozlar[:MAX_SOZLAR]
    if kod:
        rows = k.prefiks(kod)
        out.append(f"\n== kod {kod.strip()}* : {len(rows)} ta")
        limit = max(n, 30)
        out += [f"  {' | '.join(r)}" for r in rows[:limit]]
        if len(rows) > limit:
            out.append(f"  [yana {len(rows) - limit} ta — uzunroq prefiks bering]")
    for soz in sozlar:
        res = k.qidir(soz, n)
        out.append(f"\n== \"{soz}\" : {len(res)} ta eng mos")
        out += [f"  {' | '.join(r)}" for _, r in res]
        if not res:
            out.append("  topilmadi — sinonim, umumiyroq so'z yoki ruscha nomi bilan qayta qidiring")
    return "\n".join(out)


def narx_tahlili(_bilim: Bilim, args: dict) -> str:
    manbalar = args.get("manbalar")
    if not isinstance(manbalar, list) or not manbalar:
        raise VositaXatosi("'manbalar' bo'sh bo'lmagan ro'yxat bo'lishi kerak.")
    if len(manbalar) > 50:
        raise VositaXatosi("Ko'pi bilan 50 ta manba.")
    rows = []
    for i, m in enumerate(manbalar, 1):
        if not isinstance(m, dict):
            raise VositaXatosi(f"{i}-manba obyekt bo'lishi kerak: {{nomi, narx, hajm}}.")
        nomi = str(m.get("nomi") or f"manba {i}").strip()
        narx = _num(m.get("narx"), f"{i}-manba narxi")
        hajm = _num(m.get("hajm", 1), f"{i}-manba hajmi")
        if narx <= 0:
            raise VositaXatosi(f"{i}-manba narxi musbat bo'lishi kerak.")
        if hajm <= 0:
            raise VositaXatosi(f"{i}-manba hajmi musbat bo'lishi kerak.")
        rows.append((nomi, narx, hajm))
    miqdor = _num(args.get("miqdor"), "miqdor")
    if miqdor <= 0:
        raise VositaXatosi("'miqdor' musbat bo'lishi kerak.")
    t = _num(args.get("tafovut", 0), "tafovut")
    if t < 0:
        raise VositaXatosi("'tafovut' manfiy bo'lmasin.")

    X = [r[1] for r in rows]
    Y = [r[2] for r in rows]
    n = len(rows)
    obn = sum(x * y for x, y in zip(X, Y)) / sum(Y)
    sigma = math.sqrt(sum((x - obn) ** 2 for x in X) / (n - 1)) if n > 1 else 0.0
    cv = sigma / obn * 100
    bn = obn * miqdor * (1 + t / 100)

    out = ["NARX TAHLILI (VMQ-480, 30–32-bandlar) — oldindan tekshiruv; rasmiy hisob — Portal narxlar moduli", "",
           "Manbalar:"]
    for nomi, x, y in rows:
        out.append(f"  {nomi}: {_fmt(x)} so'm × hajm {y:g}")
    out += [
        "",
        f"Manbalar soni : {n}" + ("" if n >= 3 else "  ⚠ 3 tadan kam — VMQ-480 16-band: topilmaganlik dalillari kerak"),
        f"O'BN          : {_fmt(obn)} so'm  (Σ(X·Y)/ΣY)",
        f"Variatsiya    : {cv:.1f}%  " + ("OK (≤30%)" if cv <= 30 else "⚠ >30% — qayta o'rganish kerak (30-band)")
        + "  [σ maxraji n−1; modul hisobi rasmiy]",
        f"BN            : {_fmt(bn)} so'm  (O'BN × V × (1 + t/100); V={miqdor:g}, t={t:g}%)",
        f"Xavf zonasi   : shartnoma bahosi {_fmt(0.8 * obn * miqdor)} dan past yoki "
        f"{_fmt(1.1 * obn * miqdor)} dan yuqori bo'lsa — PF-259 4-\"a\" yuqori xavf",
    ]
    if t > 10:
        out.append("⚠ t > 10% — BN o'rtacha narxdan +10% dan ko'p farq qiladi (PF-259 4-\"a\")")
    return "\n".join(out)


def bilim_royxati(bilim: Bilim, _args: dict) -> str:
    return bilim.royxat()


def bilim_oqish(bilim: Bilim, args: dict) -> str:
    yol = _require_str(args, "yol")
    boshlanish = _int(args, "boshlanish", 1, 1, 10**7)
    qatorlar = _int(args, "qatorlar", 400, 1, 2000)
    return bilim.oqish(yol, boshlanish, qatorlar)


def bilim_qidirish(bilim: Bilim, args: dict) -> str:
    soz = _require_str(args, "soz")
    papka = _require_str(args, "papka", optional=True)
    n = _int(args, "n", 30, 1, 100)
    return bilim.qidirish(soz, papka, n)


# ---------------------------------------------------------------- ta'riflar

TOOL_DEFS: list[dict] = [
    {
        "name": "kod_qidir",
        "description": (
            "Mahsulot (tovar, ish, xizmat) klassifikatori bo'yicha oflayn qidiruv (24 157 pozitsiya, "
            "tasniflagich.icppa.uz). Kodni hech qachon o'ylab topmang — faqat shu vosita natijasidan oling. "
            "So'zlar o'zbekcha lotin, o'zbekcha kirill yoki ruscha bo'lishi mumkin; bir chaqiruvda bir nechta "
            "sinonim bering. 'kod' — kod prefiksi bo'yicha ro'yxat (masalan '20.41.31')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sozlar": {"type": "array", "items": {"type": "string"},
                           "description": "Qidiruv so'zlari, masalan [\"suyuq sovun\", \"мыло жидкое\"]"},
                "kod": {"type": "string", "description": "Kod prefiksi, masalan \"26.20.16\""},
                "n": {"type": "integer", "description": "Har so'z uchun natijalar soni (sukut 8, max 30)"},
            },
        },
    },
    {
        "name": "narx_tahlili",
        "description": (
            "VMQ-480 bo'yicha o'rtacha bozor narxi (O'BN), variatsiya koeffitsienti, boshlang'ich narx (BN) "
            "va PF-259 4-\"a\" xavf zonasini hisoblaydi. Narxlar QQS bilan, bir xil birlikda va bir asosga "
            "keltirilgan (yetkazib berish ichida) bo'lsin. Narxni o'ylab topmang — faqat foydalanuvchi bergan "
            "yoki manbasi aniq narxlarni kiriting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "manbalar": {
                    "type": "array",
                    "description": "Har manba: nomi, birlik narxi (so'm, QQS bilan), hajm (noma'lum bo'lsa 1)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nomi": {"type": "string"},
                            "narx": {"type": "number"},
                            "hajm": {"type": "number"},
                        },
                        "required": ["nomi", "narx"],
                    },
                },
                "miqdor": {"type": "number", "description": "V — xarid qilinadigan miqdor"},
                "tafovut": {"type": "number", "description": "t — foiz, sukut 0 (asossiz oshirmang, ≤10%)"},
            },
            "required": ["manbalar", "miqdor"],
        },
    },
    {
        "name": "bilim_royxati",
        "description": "Bilim bazasidagi fayllar ro'yxati (qonunlar, yo'riqnomalar, shablonlar, profil) — hajmi va sarlavhasi bilan.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "bilim_oqish",
        "description": (
            "Bilim bazasidagi faylni qator raqamlari bilan o'qiydi (masalan 'references/usul_hujjat_paketlari.md', "
            "'templates/tz_texnik_topshiriq.md', 'laws/11_vmq480_ortacha_bozor_narxi.md'). Katta qonun matnlarini "
            "avval bilim_qidirish bilan toping, keyin kerakli joyini 'boshlanish' bilan o'qing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "yol": {"type": "string", "description": "Bilim bazasi ildiziga nisbatan yo'l"},
                "boshlanish": {"type": "integer", "description": "Boshlang'ich qator (1 dan), sukut 1"},
                "qatorlar": {"type": "integer", "description": "Nechta qator (sukut 400, max 2000)"},
            },
            "required": ["yol"],
        },
    },
    {
        "name": "bilim_qidirish",
        "description": (
            "Bilim bazasi matnlari (SKILL.md, laws, references, templates, profil) bo'yicha qidiruv: fayl:qator "
            "va mos qator matnini qaytaradi. Lotin va kirill yozuvlari birga qidiriladi (\"narx\" → \"нарх\" "
            "ham topiladi); ruscha matnlar uchun ruscha so'z bering (masalan \"прямой договор\")."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "soz": {"type": "string", "description": "Qidiriladigan so'z yoki ibora"},
                "papka": {"type": "string", "enum": ["laws", "references", "templates", "profil"],
                          "description": "Faqat shu papkada qidirish (ixtiyoriy)"},
                "n": {"type": "integer", "description": "Natijalar soni (sukut 30, max 100)"},
            },
            "required": ["soz"],
        },
    },
]

HANDLERS: dict[str, Callable[[Bilim, dict], str]] = {
    "kod_qidir": kod_qidir,
    "narx_tahlili": narx_tahlili,
    "bilim_royxati": bilim_royxati,
    "bilim_oqish": bilim_oqish,
    "bilim_qidirish": bilim_qidirish,
}

WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
    "user_location": {"type": "approximate", "country": "UZ", "timezone": "Asia/Tashkent"},
}


def tool_params(web_search: bool) -> list[dict]:
    """API'ga yuboriladigan vositalar ro'yxati (tartib doimiy — kesh buzilmasin)."""
    tools = [{**t, "eager_input_streaming": True} for t in TOOL_DEFS]
    if web_search:
        tools.append(WEB_SEARCH_TOOL)
    return tools


def run_tool(bilim: Bilim, name: str, args: Any) -> tuple[str, bool]:
    """Vositani bajaradi. Natija: (matn, xatomi)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Noma'lum vosita: {name}", True
    if not isinstance(args, dict):
        return "Vosita kirishi JSON obyekt bo'lishi kerak.", True
    try:
        return handler(bilim, args), False
    except (VositaXatosi, BilimError) as e:
        return f"Xato: {e}", True
    except Exception as e:  # kutilmagan xato — model ko'rsin, server yiqilmasin
        return f"Ichki xato ({type(e).__name__}): {e}", True
