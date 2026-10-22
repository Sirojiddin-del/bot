import base64
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.server import create_app

from .conftest import message

PNG_1PX = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                  "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
).decode()


def settings_for(bilim_dir, **kw):
    opts = {"bilim_dir": bilim_dir, "kirish_paroli": "", "web_search": False, "zaxira_model": "default", **kw}
    return dataclasses.replace(load_settings(), **opts)


@pytest.fixture
def client_for(fake_api):
    def make(bilim_dir, **kw):
        return TestClient(create_app(settings_for(bilim_dir, **kw), client_factory=fake_api.client))
    return make


def events_of(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_index_and_status(client_for, bilim_dir):
    with client_for(bilim_dir) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Davlat xaridlari agenti" in r.text
        s = c.get("/api/holat").json()
        assert s == {"bilim": True, "klassifikator": True, "model": "claude-opus-5-5",
                     "veb_qidiruv": False, "parol_kerak": False}
        assert c.get("/docs").status_code == 404


def test_chat_streams_events(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "text", "text": "Assalomu alaykum!"}]))
    fake_api.add(message([{"type": "text", "text": "Ikkinchi javob"}]))
    with client_for(bilim_dir) as c:
        r = c.post("/api/chat", json={"matn": "Salom"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        ev = events_of(r)
        assert ev[0]["t"] == "suhbat"
        sid = ev[0]["id"]
        assert "".join(e["v"] for e in ev if e["t"] == "matn") == "Assalomu alaykum!"
        assert ev[-1]["t"] == "tugadi"
        # o'sha suhbatda davom etish — tarix yuboriladi
        r2 = c.post("/api/chat", json={"matn": "Yana", "suhbat_id": sid})
        assert events_of(r2)[0]["id"] == sid
        assert [m["role"] for m in fake_api.requests[1]["messages"]] == ["user", "assistant", "user"]
        # yangi suhbat eskisini o'chiradi
        new_id = c.post("/api/yangi", json={"suhbat_id": sid}).json()["suhbat_id"]
        assert new_id != sid and c.app.state.ombor.topish(sid) is None


def test_chat_with_image(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "text", "text": "Rasmni ko'rdim"}]))
    with client_for(bilim_dir) as c:
        r = c.post("/api/chat", json={"rasmlar": [{"media_type": "image/png", "data": PNG_1PX}]})
        assert r.status_code == 200
    content = fake_api.requests[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[1] == {"type": "text", "text": "Rasmdagi hujjatni ko'rib chiqing."}


@pytest.mark.parametrize("body,status", [
    ({"matn": "   "}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/svg+xml", "data": PNG_1PX}]}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/png", "data": "@@@"}]}, 400),
    ({"matn": "x", "rasmlar": [{"media_type": "image/png", "data": PNG_1PX}] * 6}, 422),
    ({"matn": "x" * 50_001}, 422),
    ({"matn": "x", "suhbat_id": "y" * 65}, 422),
])
def test_chat_validation(client_for, bilim_dir, body, status):
    with client_for(bilim_dir) as c:
        assert c.post("/api/chat", json=body).status_code == status


def test_chat_without_knowledge_base(client_for, tmp_path):
    with client_for(tmp_path / "yoq") as c:
        assert c.get("/api/holat").json()["bilim"] is False
        r = c.post("/api/chat", json={"matn": "salom"})
        assert r.status_code == 503 and "bilim_yukla.py" in r.json()["detail"]


def test_password(client_for, bilim_dir, fake_api):
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    with client_for(bilim_dir, kirish_paroli="maxfiy-123") as c:
        assert c.get("/api/holat").json()["parol_kerak"] is True
        assert c.post("/api/kirish", json={"parol": "xato"}).status_code == 401
        assert c.post("/api/kirish", json={"parol": "maxfiy-123"}).status_code == 200
        assert c.post("/api/chat", json={"matn": "x"}).status_code == 401
        assert c.post("/api/chat", json={"matn": "x"}, headers={"X-Kirish-Paroli": "xato"}).status_code == 401
        assert c.post("/api/yangi", json={}).status_code == 401
        r = c.post("/api/chat", json={"matn": "x"}, headers={"X-Kirish-Paroli": "maxfiy-123"})
        assert r.status_code == 200 and events_of(r)[-1]["t"] == "tugadi"


def test_busy_session_rejected(client_for, bilim_dir):
    import asyncio
    with client_for(bilim_dir) as c:
        s = c.app.state.ombor.ol("band")

        async def lock():
            await s.lock.acquire()
        asyncio.run(lock())
        r = c.post("/api/chat", json={"matn": "x", "suhbat_id": "band"})
        assert r.status_code == 409
