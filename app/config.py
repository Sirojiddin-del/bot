"""Sozlamalar — muhit o'zgaruvchilaridan o'qiladi (.env fayli ham qo'llab-quvvatlanadi)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def _zaxira(raw: str) -> str:
    """Server tomonidagi zaxira (fallback) rejimi: "default" yoki bo'sh/"yoq" — o'chirilgan."""
    raw = raw.strip()
    return "" if raw.lower() in {"", "0", "yoq", "yo'q", "no", "off", "false"} else raw


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


def load_settings() -> Settings:
    bilim = Path(os.environ.get("BILIM_PAPKASI") or ROOT / "bilim")
    if not bilim.is_absolute():
        bilim = ROOT / bilim
    return Settings(
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("EFFORT", "high"),
        max_tokens=int(os.environ.get("MAX_TOKENS", "64000")),
        max_steps=int(os.environ.get("MAX_QADAM", "25")),
        web_search=_bool("VEB_QIDIRUV", True),
        zaxira_model=_zaxira(os.environ.get("ZAXIRA_MODEL", "default")),
        bilim_dir=bilim.resolve(),
        kirish_paroli=os.environ.get("KIRISH_PAROLI", ""),
        max_sessions=int(os.environ.get("MAX_SUHBAT", "200")),
    )
