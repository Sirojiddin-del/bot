"""Veb-server: chat sahifasi va oqimli (SSE) agent API.

Ishga tushirish:  uvicorn app.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

import anthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import Agent, SuhbatOmbori
from .bilim import Bilim
from .config import Settings, load_settings

STATIC = Path(__file__).resolve().parent / "static"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT = 50_000


class Rasm(BaseModel):
    media_type: str
    data: str  # base64


class ChatSorov(BaseModel):
    suhbat_id: str | None = Field(default=None, max_length=64)
    matn: str = Field(default="", max_length=MAX_TEXT)
    rasmlar: list[Rasm] = Field(default_factory=list, max_length=MAX_IMAGES)


class YangiSorov(BaseModel):
    suhbat_id: str | None = Field(default=None, max_length=64)


class KirishSorov(BaseModel):
    parol: str = ""


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


def create_app(
    settings: Settings | None = None,
    client_factory: Callable[[], anthropic.AsyncAnthropic] = anthropic.AsyncAnthropic,
) -> FastAPI:
    settings = settings or load_settings()
    bilim = Bilim(settings.bilim_dir)
    ombor = SuhbatOmbori(settings.max_sessions)
    state: dict[str, Agent] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        agent = Agent(client_factory(), settings, bilim)
        state["agent"] = agent
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

    def check_password(parol: str | None) -> None:
        if not settings.kirish_paroli:
            return
        if not parol or not secrets.compare_digest(parol.encode(), settings.kirish_paroli.encode()):
            raise HTTPException(status_code=401, detail="Kirish paroli noto'g'ri.")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/holat")
    async def holat() -> dict:
        return {
            "bilim": bilim.mavjud(),
            "klassifikator": (bilim.root / "data" / "klassifikator.json").is_file(),
            "model": settings.model,
            "veb_qidiruv": settings.web_search,
            "parol_kerak": bool(settings.kirish_paroli),
        }

    @app.post("/api/kirish")
    async def kirish(req: KirishSorov) -> dict:
        check_password(req.parol)
        return {"ok": True}

    @app.post("/api/yangi")
    async def yangi(req: YangiSorov, x_kirish_paroli: str | None = Header(default=None)) -> dict:
        check_password(x_kirish_paroli)
        if req.suhbat_id:
            old = ombor.topish(req.suhbat_id)
            if old is not None and not old.lock.locked():
                ombor.ochir(req.suhbat_id)
        return {"suhbat_id": ombor.ol(None).id}

    @app.post("/api/chat")
    async def chat(req: ChatSorov, x_kirish_paroli: str | None = Header(default=None)) -> StreamingResponse:
        check_password(x_kirish_paroli)
        if not bilim.mavjud():
            raise HTTPException(
                status_code=503,
                detail="Bilim bazasi yuklanmagan. Serverda: python3 scripts/bilim_yukla.py "
                       "<skill papkasi yoki .skill fayli>",
            )
        content = user_content(req)
        suhbat = ombor.ol(req.suhbat_id)
        if suhbat.lock.locked():
            raise HTTPException(status_code=409, detail="Oldingi javob hali tugamagan.")
        agent = state["agent"]

        async def stream() -> AsyncIterator[str]:
            async with suhbat.lock:
                yield _sse({"t": "suhbat", "id": suhbat.id})
                async for event in agent.javob(suhbat, content):
                    yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.state.ombor = ombor
    app.state.bilim = bilim
    return app


app = create_app()
