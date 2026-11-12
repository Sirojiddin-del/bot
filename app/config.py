"""Sozlamalar — muhit o'zgaruvchilaridan o'qiladi (.env fayli ham qo'llab-quvvatlanadi)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EFFORT_DARAJALARI = ("low", "medium", "high", "xhigh", "max")
LOCALHOST = ("localhost", "127.0.0.1", "::1")


class SozlamaXatosi(SystemExit):
    """Noto'g'ri sozlama — server tushunarli xabar bilan to'xtaydi."""


def _load_dotenv(path: Path) -> None:
    """Oddiy .env o'quvchi: KALIT=qiymat qatorlari; mavjud muhit o'zgaruvchilari ustun."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "ha", "yes", "on"}


def _num(name: str, default: float, lo: float, hi: float, cast=int):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return cast(default)
    try:
        value = cast(raw)
    except ValueError:
        raise SozlamaXatosi(f"{name}={raw!r} — son bo'lishi kerak.") from None
    if not lo <= value <= hi:
        raise SozlamaXatosi(f"{name}={raw} — {lo} va {hi} oralig'ida bo'lishi kerak.")
    return value


def _zaxira(raw: str) -> str:
    """Server tomonidagi zaxira (fallback) rejimi: "default" yoki bo'sh/"yoq" — o'chirilgan."""
    raw = raw.strip()
    return "" if raw.lower() in {"", "0", "yoq", "yo'q", "no", "off", "false"} else raw


def _effort(raw: str) -> str:
    raw = raw.strip().lower() or "high"
    if raw not in EFFORT_DARAJALARI:
        raise SozlamaXatosi(f"EFFORT={raw!r} — quyidagilardan biri bo'lsin: {', '.join(EFFORT_DARAJALARI)}.")
    return raw


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    max_tokens: int
    max_steps: int
    web_search: bool
    zaxira_model: str
    bilim_dir: Path
    kirish_paroli: str
    max_sessions: int
    suhbat_muddati: float  # soat: shuncha vaqt ishlatilmagan suhbat xotiradan o'chiriladi
    ruxsat_hostlar: tuple[str, ...]  # Host sarlavhasida ruxsat etilgan nomlar; "*" — hammasi

    def hostlar(self) -> tuple[str, ...]:
        """Amaldagi ruxsat etilgan hostlar.

        Aniq ko'rsatilmagan bo'lsa: parol qo'yilgan — istalgan host; parolsiz — faqat shu kompyuter
        (aks holda tarmoqdagi istalgan kishi yoki zararli sayt API kalitingizdan foydalana oladi).
        """
        if self.ruxsat_hostlar:
            return self.ruxsat_hostlar
        return ("*",) if self.kirish_paroli else LOCALHOST


def load_settings() -> Settings:
    bilim = Path(os.environ.get("BILIM_PAPKASI") or ROOT / "bilim")
    if not bilim.is_absolute():
        bilim = ROOT / bilim
    hostlar = tuple(h.strip().lower() for h in os.environ.get("RUXSAT_HOSTLAR", "").split(",") if h.strip())
    return Settings(
        model=os.environ.get("CLAUDE_MODEL", "").strip() or "claude-opus-5-5",
        effort=_effort(os.environ.get("EFFORT", "")),
        max_tokens=_num("MAX_TOKENS", 64000, 1024, 128000),
        max_steps=_num("MAX_QADAM", 25, 1, 200),
        web_search=_bool("VEB_QIDIRUV", True),
        zaxira_model=_zaxira(os.environ.get("ZAXIRA_MODEL", "default")),
        bilim_dir=bilim.resolve(),
        kirish_paroli=os.environ.get("KIRISH_PAROLI", ""),
        max_sessions=_num("MAX_SUHBAT", 200, 1, 100000),
        suhbat_muddati=_num("SUHBAT_MUDDATI_SOAT", 12, 0.01, 24 * 365, cast=float),
        ruxsat_hostlar=hostlar,
    )
