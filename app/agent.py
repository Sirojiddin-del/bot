"""Agent tsikli: Claude bilan oqimli muloqot, vositalarni bajarish va suhbat tarixi."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import anthropic

from .bilim import Bilim
from .config import Settings
from .vositalar import run_tool, tool_params

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_JSON_RETRIES = 2

PREAMBLE = """\
Siz veb-ilovada ishlaydigan "Davlat xaridlari agenti"siz. Quyida skill yo'riqnomasi (SKILL.md) berilgan — unga to'liq amal qiling. Bu muhitga moslashtirish:

- Fayllar: yo'riqnomadagi `laws/...`, `references/...`, `templates/...`, `profil/...`, `assets/...` yo'llari — bilim bazasidagi fayllar. Ularni `bilim_oqish` bilan o'qing, `bilim_qidirish` bilan qidiring, to'liq ro'yxat — `bilim_royxati`. Kerakli faylni javobdan oldin o'qing, xotiradan aytmang.
- `scripts/kod_qidir.py` o'rniga — `kod_qidir` vositasi; `scripts/narx_tahlili.py` o'rniga — `narx_tahlili` vositasi. Skriptlarni ishga tushirib bo'lmaydi.
- lex.uz va ochiq manbalarni tekshirish — `web_search` vositasi (sozlamalarda yoqilgan bo'lsa; bo'lmasa "veb-qidiruv o'chirilgan" deb ayting).
- Jonli jurnal (§9): Artifact sahifasi, ArtifactData va rejalashtirilgan vazifalar bu muhitda YO'Q — jurnal sahifasini nashr etishga yoki yozishga urinmang. Platforma bo'yicha amaldagi kuzatuvlar uchun `assets/jurnal_boshlangich.json` ni o'qing (faqat o'qish). Yangi kuzatuvni javob oxirida "JURNAL YOZUVI" bloki qilib bering (§9 7-banddagi zaxira yo'l).
- Fayl (Word/Excel) yaratish vositasi yo'q — natijani chatda nusxa-joylashga tayyor matn qilib bering.
- Tashkilot profili: suhbatda berilgan bo'lsa — shu; bo'lmasa `profil/tashkilot_profili.md` ni o'qing (to'ldirilgan bo'lishi mumkin).
- Foydalanuvchi rasm (talabnoma surati, platforma ekrani) yuborishi mumkin — diqqat bilan o'qing, qo'lyozmadan o'qilgan son va kodlarni tekshirishni so'rang.
- Javobingiz Markdown sifatida ko'rsatiladi: jadval, ro'yxat va kod bloklari ishlaydi.
"""


def build_system(bilim: Bilim, today: dt.date) -> str:
    skill = bilim.skill_matni()
    # YAML sarlavhani (--- name/description ---) olib tashlaymiz — u skill katalogi uchun
    skill = re.sub(r"\A---\n.*?\n---\n", "", skill, count=1, flags=re.S).lstrip()
    return f"{PREAMBLE}- Bugungi sana: {today.isoformat()}.\n\n=== SKILL.md ===\n\n{skill}"


def history_content(content: list[Any]) -> list[Any]:
    """Assistent javobini tarixga qo'shishga tayyorlaydi.

    Zaxira model (fallback) javob o'rtasida ulangan bo'lsa, oxirgi ``fallback`` blokidan
    oldingi thinking/tool_use bloklari va natijasiz server-vosita chaqiruvlari tarixga
    qaytarilmaydi; matn bloklari va juftlangan server-vosita bloklari qoladi.
    """
    last_fb = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    if last_fb < 0:
        return list(content)
    result_ids = {getattr(b, "tool_use_id", None) for b in content if b.type.endswith("_tool_result")}
    kept = []
    for i, b in enumerate(content):
        if b.type == "fallback":
            continue
        if i < last_fb:
            if b.type in ("thinking", "redacted_thinking", "tool_use"):
                continue
            if b.type == "server_tool_use" and b.id not in result_ids:
                continue
            if b.type not in ("text", "server_tool_use") and not b.type.endswith("_tool_result"):
                continue
        kept.append(b)
    return kept


def _short(text: str, limit: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class Suhbat:
    id: str
    messages: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    yangilangan: float = field(default_factory=time.time)


class SuhbatOmbori:
    """Xotiradagi suhbatlar (eng eskisi chiqarib yuboriladi)."""

    def __init__(self, max_size: int):
        self.max_size = max(1, max_size)
        self._items: OrderedDict[str, Suhbat] = OrderedDict()

    def ol(self, suhbat_id: str | None) -> Suhbat:
        if suhbat_id and suhbat_id in self._items:
            s = self._items[suhbat_id]
            self._items.move_to_end(suhbat_id)
            return s
        s = Suhbat(id=suhbat_id if suhbat_id and len(suhbat_id) <= 64 else uuid.uuid4().hex)
        self._items[s.id] = s
        while len(self._items) > self.max_size:
            oldest_id, oldest = next(iter(self._items.items()))
            if oldest.lock.locked():  # javob berilayotgan suhbatni o'chirmaymiz
                self._items.move_to_end(oldest_id)
                if all(x.lock.locked() for x in self._items.values()):
                    break
                continue
            self._items.pop(oldest_id)
        return s

    def topish(self, suhbat_id: str) -> Suhbat | None:
        return self._items.get(suhbat_id)

    def ochir(self, suhbat_id: str) -> None:
        self._items.pop(suhbat_id, None)


class Agent:
    def __init__(self, client: anthropic.AsyncAnthropic, settings: Settings, bilim: Bilim):
        self.client = client
        self.settings = settings
        self.bilim = bilim
        self.tools = tool_params(settings.web_search)

    def _request(self, system: str, messages: list[dict]) -> dict:
        req: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            # Statik tizim matniga aniq kesh nuqtasi + suhbat dumi uchun avtomatik kesh
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "cache_control": {"type": "ephemeral"},
            "messages": list(messages),
            "tools": self.tools,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.settings.effort},
        }
        if self.settings.zaxira_model:
            # Xavfsizlik klassifikatori rad etsa, so'rov server tomonda zaxira modelda qayta bajariladi
            req["betas"] = [FALLBACK_BETA]
            req["fallbacks"] = self.settings.zaxira_model
        return req

    async def javob(self, suhbat: Suhbat, user_content: list[dict]) -> AsyncIterator[dict]:
        """Bitta foydalanuvchi navbati. Hodisalar (dict) oqimini qaytaradi.

        Navbat xato bilan tugasa yoki to'xtatilsa, tarix navbatdan oldingi holatiga qaytariladi —
        shunda tarixda javobsiz tool_use qolmaydi va keyingi so'rov to'g'ri bo'ladi.
        """
        messages = suhbat.messages
        start_len = len(messages)
        completed = False
        usage = {"kirish": 0, "kesh_oqildi": 0, "kesh_yozildi": 0, "chiqish": 0}
        system = build_system(self.bilim, dt.date.today())
        messages.append({"role": "user", "content": user_content})
        suhbat.yangilangan = time.time()
        try:
            json_retries = 0
            step = 0
            while True:
                if step >= self.settings.max_steps:
                    completed = True  # tarix to'g'ri: oxirida tool_result turibdi
                    yield {"t": "tugadi", "sabab": "qadam_chegarasi", "usage": usage}
                    return
                step += 1
                try:
                    async with self.client.beta.messages.stream(**self._request(system, messages)) as stream:
                        async for event in stream:
                            ev = self._stream_event(event)
                            if ev:
                                yield ev
                        msg = await stream.get_final_message()
                    json_retries = 0
                except ValueError:
                    # Oqimli vosita kirishidagi JSONni SDK umuman o'qiy olmadi — navbatni qayta so'raymiz
                    json_retries += 1
                    if json_retries > MAX_JSON_RETRIES:
                        raise
                    yield {"t": "qayta"}
                    continue

                u = msg.usage
                usage["kirish"] += u.input_tokens or 0
                usage["kesh_oqildi"] += u.cache_read_input_tokens or 0
                usage["kesh_yozildi"] += u.cache_creation_input_tokens or 0
                usage["chiqish"] += u.output_tokens or 0

                if msg.stop_reason == "refusal":
                    yield {"t": "xato", "v": "So'rov xavfsizlik tizimi tomonidan rad etildi. "
                                             "Savolni boshqacha ifodalab ko'ring."}
                    return  # finally tarixni tiklaydi

                messages.append({"role": "assistant", "content": history_content(msg.content)})

                if msg.stop_reason == "pause_turn":
                    continue  # server vositasi uzoq ishladi — davom ettiramiz

                tool_uses = [b for b in msg.content if b.type == "tool_use"]
                if msg.stop_reason == "max_tokens" and tool_uses:
                    yield {"t": "xato", "v": "Javob juda uzun bo'lib ketdi va kesildi. Savolni bo'lib bering."}
                    return
                if not tool_uses:
                    completed = True
                    yield {"t": "tugadi", "sabab": msg.stop_reason, "usage": usage}
                    return

                results = []
                for b in tool_uses:
                    yield {"t": "vosita", "id": b.id, "nom": b.name, "kirish": b.input}
                    text, is_error = await asyncio.to_thread(run_tool, self.bilim, b.name, b.input)
                    yield {"t": "natija", "id": b.id, "nom": b.name, "xato": is_error, "qisqa": _short(text)}
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": text,
                                    **({"is_error": True} if is_error else {})})
                messages.append({"role": "user", "content": results})
        except anthropic.APIStatusError as e:
            yield {"t": "xato", "v": _api_error_text(e)}
        except anthropic.APIConnectionError:
            yield {"t": "xato", "v": "Claude API bilan aloqa uzildi. Internetni tekshirib, qayta urinib ko'ring."}
        except ValueError:
            yield {"t": "xato", "v": "Model vosita chaqiruvini noto'g'ri shaklda yubordi. Qayta urinib ko'ring."}
        finally:
            if not completed:
                del messages[start_len:]
            suhbat.yangilangan = time.time()

    @staticmethod
    def _stream_event(event: Any) -> dict | None:
        et = event.type
        if et == "text":
            return {"t": "matn", "v": event.text}
        if et == "thinking":
            return {"t": "fikr", "v": event.thinking}
        if et == "content_block_start":
            b = event.content_block
            if b.type == "text":
                return {"t": "blok"}
            if b.type == "fallback":
                return {"t": "zaxira", "dan": b.from_.model, "ga": b.to.model}
            return None
        if et == "content_block_stop":
            b = event.content_block
            if b.type == "server_tool_use" and b.name == "web_search":
                query = b.input.get("query", "") if isinstance(b.input, dict) else ""
                return {"t": "qidiruv", "v": query}
            if b.type == "web_search_tool_result":
                items = b.content if isinstance(b.content, list) else []
                return {"t": "manbalar", "v": [{"nom": r.title, "url": r.url} for r in items
                                                if getattr(r, "type", "") == "web_search_result"]}
        return None


def _api_error_text(e: anthropic.APIStatusError) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "API kaliti noto'g'ri yoki o'rnatilmagan (ANTHROPIC_API_KEY)."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "API kalitida bu modelga ruxsat yo'q."
    if isinstance(e, anthropic.NotFoundError):
        return "Model topilmadi — CLAUDE_MODEL sozlamasini tekshiring."
    if isinstance(e, anthropic.RateLimitError):
        return "So'rovlar chegarasiga yetildi. Birozdan keyin qayta urinib ko'ring."
    if e.status_code >= 500:
        return f"Claude API vaqtincha ishlamayapti ({e.status_code}). Birozdan keyin qayta urinib ko'ring."
    return f"API xatosi ({e.status_code}): {e.message}"
