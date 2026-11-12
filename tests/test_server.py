import asyncio
import base64
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

import app.server as server_mod
from app.config import load_settings
from app.server import CSP, create_app

from .conftest import message

PNG_1PX = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                  "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
).decode()
HOST = {"base_url": "http://localhost"}


def settings_for(bilim_dir, **kw):
    opts = {"bilim_dir": bilim_dir, "kirish_paroli": "", "web_search": False, "zaxira_model": "default",
            "ruxsat_hostlar": (), **kw}
    return dataclasses.replace(load_settings(), **opts)


@pytest.fixture
def client_for(fake_api):
    def make(bilim_dir, base_url="http://localhost", **kw):
        return TestClient(create_app(settings_for(bilim_dir, **kw), client_factory=fake_api.client),
                          base_url=base_url)
    return make


def events_of(resp):
    return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]


def login(c, parol):
    r = c.post("/api/kirish", json={"parol": parol})
    assert r.status_code == 200
    return {"X-Kirish-Token": r.json()["token"]}


# ---------------------------------------------------------------- sahifa va holat

def test_index_static_and_status(client_for, bilim_dir):
    with client_for(bilim_dir) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Davlat xaridlari agenti" in r.text
        # sahifada ichki (inline) skript/uslub yo'q — CSP ularni baribir bloklaydi
        assert "<script>" not in r.text and "<style>" not in r.text and "cdn." not in r.text
        for path in ("/static/app.js", "/static/style.css", "/static/vendor/marked.min.js",
                     "/static/vendor/purify.min.js"):
            assert c.get(path).status_code == 200, path
        assert c.get("/static/../server.py").status_code == 404
        s = c.get("/api/holat").json()
        assert s == {"parol_kerak": False, "kirilgan": True, "bilim": True, "klassifikator": True,
                     "kalit": True, "model": "claude-opus-5-5", "veb_qidiruv": False}
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_security_headers_on_every_response(client_for, bilim_dir):
    with client_for(bilim_dir) as c:
        for r in (c.get("/"), c.get("/static/app.js"), c.get("/api/holat"), c.get("/yoq"),
                  c.post("/api/chat", json={"matn": ""})):
            assert r.headers["content-security-policy"] == CSP
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["referrer-policy"] == "no-referrer"
    assert "script-src 'self'" in CSP and "unsafe-inline" not in CSP and "form-action 'none'" in CSP


# ---------------------------------------------------------------- chat

def test_chat_streams_events(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "text", "text": "Assalomu alaykum!"}]))
    fake_api.add(message([{"type": "text", "text": "Ikkinchi javob"}]))
    with client_for(bilim_dir) as c:
        r = c.post("/api/chat", json={"matn": "Salom"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        ev = events_of(r)
        assert ev[0]["t"] == "suhbat" and "yoqolgan" not in ev[0]
        sid = ev[0]["id"]
        assert "".join(e["v"] for e in ev if e["t"] == "matn") == "Assalomu alaykum!"
        assert ev[-1]["t"] == "tugadi"
        # o'sha suhbatda davom etish — tarix yuboriladi
        r2 = c.post("/api/chat", json={"matn": "Yana", "suhbat_id": sid})
        assert events_of(r2)[0] == {"t": "suhbat", "id": sid}
        assert [m["role"] for m in fake_api.requests[1]["messages"]] == ["user", "assistant", "user"]
        # "Yangi suhbat" eskisini xotiradan o'chiradi
        assert c.post("/api/yangi", json={"suhbat_id": sid}).json() == {"ok": True}
        assert c.app.state.ombor.topish(sid) is None
        assert c.post("/api/yangi", json={"suhbat_id": "yoq"}).status_code == 200


def test_unknown_session_gets_new_id_and_is_flagged(client_for, bilim_dir, fake_api):
    """Server qayta ishga tushgach eski suhbat_id kelsa — tarix yo'qligi mijozga aytiladi."""
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    with client_for(bilim_dir) as c:
        ev = events_of(c.post("/api/chat", json={"matn": "davomi", "suhbat_id": "eski-suhbat"}))
        assert ev[0]["yoqolgan"] is True and ev[0]["id"] != "eski-suhbat"
        assert c.app.state.ombor.topish("eski-suhbat") is None
        assert c.app.state.ombor.topish(ev[0]["id"]) is not None


def test_chat_with_image(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "text", "text": "Rasmni ko'rdim"}]))
    with client_for(bilim_dir) as c:
        r = c.post("/api/chat", json={"rasmlar": [{"media_type": "image/png", "data": PNG_1PX}]})
        assert r.status_code == 200
        s = c.app.state.ombor.topish(events_of(r)[0]["id"])
        assert (s.rasm_soni, s.rasm_hajmi) == (1, len(PNG_1PX))
    content = fake_api.requests[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[1] == {"type": "text", "text": "Rasmdagi hujjatni ko'rib chiqing."}


@pytest.mark.parametrize("body,status", [
    ({"matn": "   "}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/svg+xml", "data": PNG_1PX}]}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/png", "data": "@@@"}]}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/png", "data": ""}]}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/png", "data": PNG_1PX}] * 6}, 422),
    ({"matn": "x" * 50_001}, 422),
    ({"matn": "x", "suhbat_id": "y" * 65}, 422),
])
def test_chat_validation(client_for, bilim_dir, body, status):
    with client_for(bilim_dir) as c:
        assert c.post("/api/chat", json=body).status_code == status


def test_images_per_conversation_are_limited(client_for, bilim_dir, fake_api, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_SUHBAT_RASM", 3)
    img = {"media_type": "image/png", "data": PNG_1PX}
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    fake_api.add(message([{"type": "text", "text": "matn hali ham ishlaydi"}]))
    with client_for(bilim_dir) as c:
        sid = events_of(c.post("/api/chat", json={"rasmlar": [img, img]}))[0]["id"]
        r = c.post("/api/chat", json={"rasmlar": [img, img], "suhbat_id": sid})
        assert r.status_code == 400 and "yangi suhbat" in r.json()["detail"]
        assert len(fake_api.requests) == 1  # rad etilgan xabar Claude'ga yuborilmadi
        assert c.post("/api/chat", json={"matn": "rasmsiz", "suhbat_id": sid}).status_code == 200
    # hajm bo'yicha chegara
    monkeypatch.setattr(server_mod, "MAX_SUHBAT_RASM_B64", len(PNG_1PX) + 1)
    with client_for(bilim_dir) as c:
        assert c.post("/api/chat", json={"rasmlar": [img, img]}).status_code == 400


def test_chat_without_knowledge_base(client_for, tmp_path):
    with client_for(tmp_path / "yoq") as c:
        assert c.get("/api/holat").json()["bilim"] is False
        r = c.post("/api/chat", json={"matn": "salom"})
        assert r.status_code == 503 and "bilim_yukla.py" in r.json()["detail"]


def test_busy_session_waits_then_reports(client_for, bilim_dir, fake_api, monkeypatch):
    monkeypatch.setattr(server_mod, "LOCK_WAIT", 0.05)
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    with client_for(bilim_dir) as c:
        s = c.app.state.ombor.yarat()
        asyncio.run(s.lock.acquire())
        ev = events_of(c.post("/api/chat", json={"matn": "x", "suhbat_id": s.id}))
        assert [e["t"] for e in ev] == ["suhbat", "xato"] and "hali tugamagan" in ev[1]["v"]
        assert s.messages == [] and fake_api.requests == []
        # "Yangi suhbat" javob paytida bosilsa — suhbat javob tugagach o'chiriladi
        assert c.post("/api/yangi", json={"suhbat_id": s.id}).status_code == 200
        assert s.ochirilsin and c.app.state.ombor.topish(s.id) is s
        s.lock.release()
        ev = events_of(c.post("/api/chat", json={"matn": "x", "suhbat_id": s.id}))
        assert ev[-1]["t"] == "tugadi"
        assert c.app.state.ombor.topish(s.id) is None


def test_lock_released_and_history_rolled_back_after_api_error(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "bilim_royxati", "input": {}}], stop_reason="tool_use"))
    # ikkinchi so'rovga javob yo'q -> soxta API 500 qaytaradi
    with client_for(bilim_dir) as c:
        ev = events_of(c.post("/api/chat", json={"matn": "x"}))
        assert ev[-1]["t"] == "xato"
        s = c.app.state.ombor.topish(ev[0]["id"])
        assert s.messages == [] and not s.lock.locked()


# ---------------------------------------------------------------- kirish (parol, token)

def test_password_login_and_token(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    with client_for(bilim_dir, kirish_paroli="maxfiy-123") as c:
        # kirmagan mijozga faqat "parol kerak" deyiladi — model va bilim bazasi holati ko'rsatilmaydi
        assert c.get("/api/holat").json() == {"parol_kerak": True, "kirilgan": False}
        assert c.post("/api/kirish", json={"parol": "xato"}).status_code == 401
        assert c.post("/api/chat", json={"matn": "x"}).status_code == 401
        assert c.post("/api/chat", json={"matn": "x"}, headers={"X-Kirish-Token": "soxta"}).status_code == 401
        # parolning o'zi token o'rnida o'tmaydi
        assert c.post("/api/chat", json={"matn": "x"}, headers={"X-Kirish-Token": "maxfiy-123"}).status_code == 401
        assert c.post("/api/yangi", json={}).status_code == 401
        assert fake_api.requests == []
        h = login(c, "maxfiy-123")
        assert len(h["X-Kirish-Token"]) >= 40
        assert c.get("/api/holat", headers=h).json()["kirilgan"] is True
        r = c.post("/api/chat", json={"matn": "x"}, headers=h)
        assert r.status_code == 200 and events_of(r)[-1]["t"] == "tugadi"
        assert c.post("/api/yangi", json={}, headers=h).status_code == 200


def test_non_ascii_password_works_end_to_end(client_for, bilim_dir, fake_api):
    """Parol JSON tanada ketadi, keyin ASCII token ishlatiladi — oʻ, gʻ va kirill harflari muammo emas."""
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    with client_for(bilim_dir, kirish_paroli="Oʻzbekiston-Тошкент-2026") as c:
        h = login(c, "Oʻzbekiston-Тошкент-2026")
        h["X-Kirish-Token"].encode("ascii")
        assert c.post("/api/chat", json={"matn": "x"}, headers=h).status_code == 200


def test_token_expires(client_for, bilim_dir, monkeypatch):
    with client_for(bilim_dir, kirish_paroli="p") as c:
        h = login(c, "p")
        assert c.post("/api/yangi", json={}, headers=h).status_code == 200
        monkeypatch.setattr(server_mod, "TOKEN_TTL", -1)
        h2 = login(c, "p")
        assert c.post("/api/yangi", json={}, headers=h2).status_code == 401


def test_password_guessing_is_throttled(client_for, bilim_dir, monkeypatch):
    with client_for(bilim_dir, kirish_paroli="togri-parol") as c:
        for _ in range(server_mod.XATO_CHEGARASI):
            assert c.post("/api/kirish", json={"parol": "taxmin"}).status_code == 401
        r = c.post("/api/kirish", json={"parol": "togri-parol"})  # blok paytida to'g'ri parol ham o'tmaydi
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
        # blok muddati o'tgach yana kirish mumkin
        kirish = c.app.state.kirish
        kirish._xatolar = {k: (0, 0.0) for k in kirish._xatolar}
        assert c.post("/api/kirish", json={"parol": "togri-parol"}).status_code == 200
        # muvaffaqiyatli kirish xatolar hisobini nolga tushiradi
        for _ in range(server_mod.XATO_CHEGARASI - 1):
            assert c.post("/api/kirish", json={"parol": "taxmin"}).status_code == 401
        assert c.post("/api/kirish", json={"parol": "togri-parol"}).status_code == 200
        assert c.post("/api/kirish", json={"parol": "x" * 300}).status_code == 422


# ---------------------------------------------------------------- so'rov hajmi

def test_oversized_bodies_are_rejected_before_parsing(client_for, bilim_dir, fake_api, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_BODY_CHAT", 2000)
    monkeypatch.setattr(server_mod, "MAX_BODY_OTHER", 100)
    with client_for(bilim_dir) as c:
        assert c.post("/api/chat", json={"matn": "x" * 3000}).status_code == 413
        assert c.post("/api/kirish", json={"parol": "x" * 200}).status_code == 413
        assert c.post("/api/yangi", json={"suhbat_id": "x" * 60, "ortiqcha": "y" * 100}).status_code == 413
        # Content-Length ko'rsatilmagan (chunked) tana ham oqim davomida sanaladi
        def bolaklar():
            for _ in range(30):
                yield b"x" * 100
        r = c.post("/api/chat", content=bolaklar(), headers={"content-type": "application/json"})
        assert r.status_code == 413 and "transfer-encoding" not in r.headers
        assert c.post("/api/chat", content=b"{}", headers={"content-length": "abc"}).status_code in (400, 413)
        assert fake_api.requests == []


def test_unauthenticated_client_cannot_make_server_buffer_a_body(client_for, bilim_dir, monkeypatch):
    """Tokensiz so'rov tanasi FastAPI ga yetib bormaydi (xotiraga yig'ilmaydi va tahlil qilinmaydi)."""
    seen = []
    original = server_mod.user_content
    monkeypatch.setattr(server_mod, "user_content", lambda req: seen.append(req) or original(req))
    with client_for(bilim_dir, kirish_paroli="p") as c:
        # noto'g'ri JSON: FastAPI tanani o'qiganida 422 bergan bo'lardi — 401 esa tana o'qilmaganini bildiradi
        r = c.post("/api/chat", content=b"{" + b"x" * 500_000, headers={"content-type": "application/json"})
        assert r.status_code == 401 and seen == []


# ---------------------------------------------------------------- Host tekshiruvi

def test_passwordless_server_is_localhost_only(client_for, bilim_dir):
    for url in ("http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"):
        with client_for(bilim_dir, base_url=url) as c:
            assert c.get("/api/holat").status_code == 200, url
    for url in ("http://attacker.example", "http://192.168.1.5:8000", "http://localhost.evil.example"):
        with client_for(bilim_dir, base_url=url) as c:
            r = c.get("/")
            assert r.status_code == 400 and "KIRISH_PAROLI" in r.text, url
            assert c.post("/api/chat", json={"matn": "x"}).status_code == 400
            assert c.get("/static/app.js").status_code == 400


def test_allowed_hosts_setting_and_password_mode(client_for, bilim_dir):
    with client_for(bilim_dir, base_url="http://xarid.example.uz", ruxsat_hostlar=("xarid.example.uz", "*.ichki.uz")) as c:
        assert c.get("/").status_code == 200
    with client_for(bilim_dir, base_url="http://a.ichki.uz:8000", ruxsat_hostlar=("xarid.example.uz", "*.ichki.uz")) as c:
        assert c.get("/").status_code == 200
    with client_for(bilim_dir, base_url="http://boshqa.uz", ruxsat_hostlar=("xarid.example.uz", "*.ichki.uz")) as c:
        assert c.get("/").status_code == 400
    # parol qo'yilgan bo'lsa, istalgan manzildan ochiladi (kirish paroli bilan)
    with client_for(bilim_dir, base_url="http://192.168.1.5:8000", kirish_paroli="p") as c:
        assert c.get("/").status_code == 200
