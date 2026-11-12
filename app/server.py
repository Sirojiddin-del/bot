"""Veb-server: chat sahifasi va oqimli (SSE) agent API.

Ishga tushirish:  uvicorn app.server:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import secrets
import time
from collections import OrderedDict
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

import anthropic
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent, SuhbatOmbori
from .bilim import Bilim
from .config import Settings, load_settings

log = logging.getLogger("xarid_agent")

STATIC = Path(__file__).resolve().parent / "static"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_TEXT = 50_000
MAX_IMAGES = 5  # bitta xabarda
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # bitta rasm (API chegarasi)
MAX_IMAGE_B64 = 7_000_000  # 5 MB rasmning base64 uzunligi (zaxira bilan)
# Rasmlar tarixda qoladi va har so'rovda qayta yuboriladi. API so'rovi 32 MB dan oshmasligi kerak,
# 20 tadan ko'p rasmli so'rovda esa har rasm 2000 px dan kichik bo'lishi shart — shuning uchun
# bitta suhbatdagi rasmlar soni va hajmi cheklanadi.
MAX_SUHBAT_RASM = 20
MAX_SUHBAT_RASM_B64 = 20_000_000
MAX_BODY_CHAT = 22 * 1024 * 1024
MAX_BODY_OTHER = 16 * 1024
LOCK_WAIT = 3.0  # soniya: to'xtatilgan oldingi javob yakunlanishini shuncha kutamiz
TOKEN_TTL = 7 * 24 * 3600
MAX_TOKENLAR = 5000
XATO_CHEGARASI = 5  # ketma-ket noto'g'ri parol
XATO_BLOKI = 60  # soniya

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
XAVFSIZLIK_SARLAVHALARI = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-cache"),
]
TOKEN_YOLLARI = {"/api/chat", "/api/yangi"}
HOST_XATOSI = (
    "Bu manzil orqali kirish yopiq.\n\n"
    "Parol qo'yilmagan server faqat shu kompyuterdan (http://localhost yoki http://127.0.0.1) ochiladi.\n"
    "Boshqa manzildan kirish uchun .env faylida KIRISH_PAROLI qo'ying "
    "yoki RUXSAT_HOSTLAR ga shu manzilni yozing, so'ng serverni qayta ishga tushiring.\n"
)


class Rasm(BaseModel):
    media_type: str = Field(max_length=32)
    data: str = Field(max_length=MAX_IMAGE_B64)  # base64


class ChatSorov(BaseModel):
    suhbat_id: str | None = Field(default=None, max_length=64)
    matn: str = Field(default="", max_length=MAX_TEXT)
    rasmlar: list[Rasm] = Field(default_factory=list, max_length=MAX_IMAGES)


class YangiSorov(BaseModel):
    suhbat_id: str | None = Field(default=None, max_length=64)


class KirishSorov(BaseModel):
    parol: str = Field(default="", max_length=256)


def user_content(req: ChatSorov) -> list[dict]:
    """So'rovdan Claude uchun foydalanuvchi xabari (rasmlar + matn) yasaydi."""
    content: list[dict] = []
    for r in req.rasmlar:
        if r.media_type not in IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Faqat JPEG, PNG, GIF yoki WEBP rasm yuboring.")
        try:
            raw = base64.b64decode(r.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Rasm ma'lumoti buzilgan.") from None
        if not raw:
            raise HTTPException(status_code=400, detail="Rasm bo'sh.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Har bir rasm 5 MB dan oshmasin.")
        content.append({"type": "image", "source": {"type": "base64", "media_type": r.media_type, "data": r.data}})
    text = req.matn.strip()
    if not text and not content:
        raise HTTPException(status_code=400, detail="Xabar bo'sh.")
    content.append({"type": "text", "text": text or "Rasmdagi hujjatni ko'rib chiqing."})
    return content


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


class Kirish:
    """Parol bilan kirish: to'g'ri paroldan so'ng tasodifiy token beriladi (parol har so'rovda yuborilmaydi)."""

    def __init__(self, parol: str):
        self.parol = parol
        self._tokenlar: OrderedDict[str, float] = OrderedDict()  # token -> amal qilish muddati
        self._xatolar: dict[str, tuple[int, float]] = {}  # manzil -> (xatolar soni, blok tugashi)

    @property
    def kerak(self) -> bool:
        return bool(self.parol)

    def token_togri(self, token: str | None) -> bool:
        if not self.kerak:
            return True
        muddat = self._tokenlar.get(token) if token else None
        if muddat is None:
            return False
        if muddat < time.time():
            del self._tokenlar[token]
            return False
        return True

    def kir(self, parol: str, manzil: str) -> str:
        """Parolni tekshiradi va token qaytaradi. Parolni taxmin qilishga qarshi: 5 xatodan so'ng 60 s blok."""
        if not self.kerak:
            return ""
        now = time.time()
        soni, blok = self._xatolar.get(manzil, (0, 0.0))
        if blok > now:
            qoldi = int(blok - now) + 1
            raise HTTPException(status_code=429, headers={"Retry-After": str(qoldi)},
                                detail=f"Juda ko'p noto'g'ri urinish. {qoldi} soniyadan keyin qayta urinib ko'ring.")
        if not secrets.compare_digest(parol.encode(), self.parol.encode()):
            soni += 1
            self._xatolar[manzil] = (0, now + XATO_BLOKI) if soni >= XATO_CHEGARASI else (soni, 0.0)
            if len(self._xatolar) > 10_000:  # xotira cheksiz o'smasin
                self._xatolar = {k: v for k, v in self._xatolar.items() if v[1] > now}
            raise HTTPException(status_code=401, detail="Kirish paroli noto'g'ri.")
        self._xatolar.pop(manzil, None)
        token = secrets.token_urlsafe(32)
        self._tokenlar[token] = now + TOKEN_TTL
        while len(self._tokenlar) > MAX_TOKENLAR:
            self._tokenlar.popitem(last=False)
        return token


def _host_nomi(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # IPv6: [::1]:8000
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


async def _javob(send, status: int, matn: str, json_detail: bool = True) -> None:
    body = (json.dumps({"detail": matn}, ensure_ascii=False) if json_detail else matn).encode()
    ctype = b"application/json" if json_detail else b"text/plain; charset=utf-8"
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _tashla(receive, chegara: int) -> None:
    """So'rov tanasini xotiraga yig'masdan o'qib tashlaydi — brauzer javobni to'g'ri olishi uchun."""
    oqildi = 0
    while oqildi <= chegara:
        msg = await receive()
        if msg["type"] != "http.request":
            return
        oqildi += len(msg.get("body", b""))
        if not msg.get("more_body"):
            return


class Himoya:
    """Sof ASGI qatlami. FastAPI so'rov tanasini o'qishidan OLDIN ishlaydi:

    - Host sarlavhasini tekshiradi (DNS rebinding va tarmoqdan parolsiz kirishga qarshi);
    - token talab qilinadigan yo'llarda tokenni tekshiradi (parolsiz mijoz serverga katta tana yuklata olmaydi);
    - so'rov tanasi hajmini cheklaydi (Content-Length bo'yicha ham, oqim davomida ham);
    - barcha javoblarga xavfsizlik sarlavhalarini qo'shadi.
    """

    def __init__(self, app, kirish: Kirish, hostlar: tuple[str, ...]):
        self.app = app
        self.kirish = kirish
        self.hostlar = tuple(h.strip("[]") for h in hostlar)

    def _host_togri(self, host: str) -> bool:
        if "*" in self.hostlar:
            return True
        nom = _host_nomi(host)
        return any(nom == h or (h.startswith("*.") and nom.endswith(h[1:])) for h in self.hostlar)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rad_etildi = False

        async def ozimiz(message):
            """Javobni xavfsizlik sarlavhalari bilan yuboradi."""
            if message["type"] == "http.response.start":
                hs = list(message.get("headers", []))
                bor = {k.lower() for k, _ in hs}
                message = {**message, "headers": hs + [(k, v) for k, v in XAVFSIZLIK_SARLAVHALARI if k not in bor]}
            await send(message)

        async def yubor(message):
            if not rad_etildi:  # aks holda javobni o'zimiz berib bo'ldik — ilovaning kechikkan javobi tashlanadi
                await ozimiz(message)

        if not self._host_togri(headers.get("host", "")):
            await _javob(ozimiz, 400, HOST_XATOSI, json_detail=False)
            return
        if scope["method"] != "POST":
            await self.app(scope, receive, yubor)
            return

        path = scope["path"]
        chegara = MAX_BODY_CHAT if path == "/api/chat" else MAX_BODY_OTHER
        try:
            elon = int(headers.get("content-length") or 0)
        except ValueError:
            elon = chegara + 1
        if elon > chegara:
            await _javob(ozimiz, 413, "So'rov juda katta.")
            return
        if path in TOKEN_YOLLARI and not self.kirish.token_togri(headers.get("x-kirish-token")):
            await _tashla(receive, chegara)
            await _javob(ozimiz, 401, "Kirish talab qilinadi.")
            return

        oqildi = 0

        async def qabul():
            nonlocal oqildi, rad_etildi
            if rad_etildi:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                oqildi += len(msg.get("body", b""))
                if oqildi > chegara:  # Content-Length ko'rsatilmagan (chunked) katta tana
                    rad_etildi = True
                    await _javob(ozimiz, 413, "So'rov juda katta.")
                    return {"type": "http.disconnect"}
            return msg

        await self.app(scope, qabul, yubor)


def create_app(
    settings: Settings | None = None,
    client_factory: Callable[[], anthropic.AsyncAnthropic] = anthropic.AsyncAnthropic,
) -> FastAPI:
    settings = settings or load_settings()
    bilim = Bilim(settings.bilim_dir)
    ombor = SuhbatOmbori(settings.max_sessions, ttl=settings.suhbat_muddati * 3600)
    kirish_xizmati = Kirish(settings.kirish_paroli)
    state: dict[str, Agent] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        agent = Agent(client_factory(), settings, bilim)
        state["agent"] = agent
        if not kirish_xizmati.kerak:
            log.warning("KIRISH_PAROLI o'rnatilmagan — ruxsat etilgan manzillar: %s", ", ".join(settings.hostlar()))
        preload = None
        if bilim.mavjud() and (bilim.root / "data" / "klassifikator.json").is_file():
            # Klassifikatorni oldindan yuklab qo'yamiz — birinchi kod qidiruvi tez bo'lsin
            preload = asyncio.create_task(asyncio.to_thread(lambda: bilim.klassifikator))
        yield
        if preload and not preload.done():
            preload.cancel()
        await agent.client.close()

    app = FastAPI(title="Davlat xaridlari agenti", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(Himoya, kirish=kirish_xizmati, hostlar=settings.hostlar())
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def token_kerak(token: str | None) -> None:
        if not kirish_xizmati.token_togri(token):
            raise HTTPException(status_code=401, detail="Kirish talab qilinadi.")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/holat")
    async def holat(x_kirish_token: str | None = Header(default=None)) -> dict:
        if not kirish_xizmati.token_togri(x_kirish_token):
            return {"parol_kerak": True, "kirilgan": False}
        return {
            "parol_kerak": kirish_xizmati.kerak,
            "kirilgan": True,
            "bilim": bilim.mavjud(),
            "klassifikator": (bilim.root / "data" / "klassifikator.json").is_file(),
            "model": settings.model,
            "veb_qidiruv": settings.web_search,
        }

    @app.post("/api/kirish")
    async def kirish(req: KirishSorov, request: Request) -> dict:
        manzil = request.client.host if request.client else "?"
        return {"ok": True, "token": kirish_xizmati.kir(req.parol, manzil)}

    @app.post("/api/yangi")
    async def yangi(req: YangiSorov, x_kirish_token: str | None = Header(default=None)) -> dict:
        """Eski suhbatni xotiradan o'chiradi (yangisi birinchi xabarda yaratiladi)."""
        token_kerak(x_kirish_token)
        eski = ombor.topish(req.suhbat_id)
        if eski is not None:
            if eski.lock.locked():
                eski.ochirilsin = True  # javob hali ketyapti — tugashi bilan o'chiriladi
            else:
                ombor.ochir(eski.id)
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(req: ChatSorov, x_kirish_token: str | None = Header(default=None)) -> StreamingResponse:
        token_kerak(x_kirish_token)
        if not bilim.mavjud():
            raise HTTPException(
                status_code=503,
                detail="Bilim bazasi yuklanmagan. Serverda: python3 scripts/bilim_yukla.py "
                       "<skill papkasi yoki .skill fayli>",
            )
        content = user_content(req)
        suhbat = ombor.topish(req.suhbat_id)
        # Mijoz yuborgan suhbat serverda yo'q (server qayta ishga tushgan yoki muddati o'tgan):
        # yangi suhbat ochamiz va buni mijozga bildiramiz — agent oldingi xabarlarni "eslamaydi".
        yoqolgan = bool(req.suhbat_id) and suhbat is None
        if suhbat is None:
            suhbat = ombor.yarat()
        yangi_rasmlar = [b for b in content if b["type"] == "image"]
        if yangi_rasmlar:
            hajm = sum(len(b["source"]["data"]) for b in yangi_rasmlar)
            if (suhbat.rasm_soni + len(yangi_rasmlar) > MAX_SUHBAT_RASM
                    or suhbat.rasm_hajmi + hajm > MAX_SUHBAT_RASM_B64):
                raise HTTPException(
                    status_code=400,
                    detail=f"Bitta suhbatda ko'pi bilan {MAX_SUHBAT_RASM} ta rasm (jami ~15 MB) yuborish mumkin. "
                           "Davom etish uchun yangi suhbat boshlang.",
                )
        agent = state["agent"]

        async def stream() -> AsyncIterator[str]:
            boshlanish = {"t": "suhbat", "id": suhbat.id, **({"yoqolgan": True} if yoqolgan else {})}
            try:
                await asyncio.wait_for(suhbat.lock.acquire(), LOCK_WAIT)
            except asyncio.TimeoutError:
                yield _sse(boshlanish)
                yield _sse({"t": "xato", "v": "Bu suhbatda oldingi javob hali tugamagan. Biroz kutib, qayta yuboring."})
                return
            try:
                yield _sse(boshlanish)
                # aclosing: mijoz uzilsa, avval agent navbati yopiladi (tarix tiklanadi), keyin qulf bo'shaydi
                async with aclosing(agent.javob(suhbat, content)) as hodisalar:
                    async for event in hodisalar:
                        yield _sse(event)
            finally:
                suhbat.lock.release()
                if suhbat.ochirilsin:
                    ombor.ochir(suhbat.id)
                ombor.tozala()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.state.ombor = ombor
    app.state.bilim = bilim
    app.state.kirish = kirish_xizmati
    return app


app = create_app()
