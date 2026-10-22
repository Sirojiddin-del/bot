"""Testlar uchun umumiy fixture'lar: kichik sun'iy bilim bazasi va soxta Claude API."""
from __future__ import annotations

import json
from pathlib import Path

import anthropic
import httpx2
import pytest

SKILL_MD = """---
name: test-skill
description: "Test"
---

# Davlat xaridlari agenti (test)

O'rtacha bozor narxi — `laws/11_vmq480.md`.
"""

LAW_MD = """# VMQ-480 (test)

30. Ўзгариш коэффициенти 30 фоиздан ошмаслиги керак.
31. Ўртача бозор нархи формуласи.
32. Бошланғич нарх: БН = (ЎБН × V) × (1 + т/100).
"""

REF_MD = """# Usullar

Kichik qiymatli xarid — komissiyasiz.
Elektron do'kon — standart tovarlar uchun.
Прямой договор заключается по статье 71.
"""

KLASSIFIKATOR = {
    "v": 1,
    "source": "test",
    "fetched": "2026-09-18",
    "items": [
        ["20.41.31.130-00002", "Pastasimon suyuq sovun", "Мыло жидкое пастообразное", 1, "Y", 1],
        ["20.41.31.130-00001", "Yuvinish sovuni, suyuq", "Мыло туалетное жидкое", 2, "Y", 2],
        ["20.11.11.140-00002", "Suyuq azot", "Азот жидкий", 3, "Y", 3],
        ["28.25.12.130-00001", "Maishiy konditsioner", "Кондиционер бытовой", 4, "Y", 4],
        ["29.32.30.269-00003", "Konditsioner filtri", "Фильтр кондиционера", 5, "Y", 5],
        ["13.92.12.119-00001", "Choyshab", "Белье постельное", 6, "Y", 6],
        ["17.22.11.130-00003", "Qog‘oz sochiq", "Полотенце бумажное", 7, "Y", 7],
    ],
}


@pytest.fixture
def bilim_dir(tmp_path: Path) -> Path:
    root = tmp_path / "bilim"
    (root / "laws").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "templates").mkdir()
    (root / "data").mkdir()
    (root / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (root / "laws" / "11_vmq480.md").write_text(LAW_MD, encoding="utf-8")
    (root / "references" / "usullar.md").write_text(REF_MD, encoding="utf-8")
    (root / "templates" / "katta.md").write_text(
        "# Katta\n" + "\n".join(f"qator {i}" for i in range(2, 1001)), encoding="utf-8"
    )
    (root / "data" / "klassifikator.json").write_text(json.dumps(KLASSIFIKATOR, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "maxfiy.md").write_text("maxfiy", encoding="utf-8")
    return root


# ---------------------------------------------------------------- soxta Claude API

def sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


def message(blocks: list[dict], stop_reason: str = "end_turn", usage: dict | None = None,
            stop_details: dict | None = None) -> list[dict]:
    """Bloklar ro'yxatidan Anthropic oqim hodisalarini yasaydi.

    Blok turlari: {"type":"text","text":...}, {"type":"thinking","thinking":...},
    {"type":"tool_use","id":...,"name":...,"input":{...}}, yoki istalgan boshqa blok (o'zgarishsiz).
    """
    u = {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10}
    u.update(usage or {})
    events: list[dict] = [{
        "type": "message_start",
        "message": {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                    "content": [], "stop_reason": None, "stop_sequence": None, "usage": u},
    }]
    for i, b in enumerate(blocks):
        t = b["type"]
        if t == "text":
            events.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            for chunk in (b["text"][: len(b["text"]) // 2], b["text"][len(b["text"]) // 2:]):
                events.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": chunk}})
        elif t == "thinking":
            events.append({"type": "content_block_start", "index": i,
                           "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "thinking_delta", "thinking": b["thinking"]}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "signature_delta", "signature": "sig123"}})
        elif t == "tool_use":
            events.append({"type": "content_block_start", "index": i,
                           "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta",
                                     "partial_json": b.get("raw_json", json.dumps(b["input"], ensure_ascii=False))}})
        else:
            events.append({"type": "content_block_start", "index": i, "content_block": b})
        events.append({"type": "content_block_stop", "index": i})
    delta = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details:
        delta["stop_details"] = stop_details
    events.append({"type": "message_delta", "delta": delta, "usage": {"output_tokens": 20}})
    events.append({"type": "message_stop"})
    return events


class FakeAPI:
    """Skriptlangan javoblar beradigan va so'rovlarni yozib boradigan soxta transport."""

    def __init__(self):
        self.responses: list = []
        self.requests: list[dict] = []
        self.headers: list[dict] = []

    def add(self, events_or_response) -> None:
        self.responses.append(events_or_response)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        if not self.responses:
            return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "no script"}})
        item = self.responses.pop(0)
        if isinstance(item, httpx2.Response):
            return item
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(item))

    def client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key="test-key",
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.handler)),
        )


@pytest.fixture
def fake_api() -> FakeAPI:
    return FakeAPI()
