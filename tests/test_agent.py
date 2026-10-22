import asyncio
import dataclasses
from types import SimpleNamespace as NS

import httpx2
import pytest

from app.agent import FALLBACK_BETA, Agent, Suhbat, SuhbatOmbori, build_system, history_content
from app.bilim import Bilim
from app.config import load_settings

from .conftest import message


def run(coro):
    return asyncio.run(coro)


async def collect(agen):
    return [ev async for ev in agen]


def make_agent(fake_api, bilim_dir, **overrides):
    opts = {"bilim_dir": bilim_dir, "web_search": True, "zaxira_model": "default", "max_steps": 25, **overrides}
    settings = dataclasses.replace(load_settings(), **opts)
    return Agent(fake_api.client(), settings, Bilim(bilim_dir))


def user(text):
    return [{"type": "text", "text": text}]


def test_build_system_strips_frontmatter(bilim_dir):
    import datetime as dt
    s = build_system(Bilim(bilim_dir), dt.date(2026, 10, 22))
    assert "name: test-skill" not in s
    assert "Bugungi sana: 2026-10-22" in s
    assert "# Davlat xaridlari agenti (test)" in s
    assert "kod_qidir" in s


def test_simple_turn_request_shape(fake_api, bilim_dir):
    fake_api.add(message([{"type": "thinking", "thinking": "o'ylayman"}, {"type": "text", "text": "Salom, dunyo!"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s1")
    events = run(collect(agent.javob(s, user("Salom"))))

    types = [e["t"] for e in events]
    assert "fikr" in types and types[-1] == "tugadi"
    assert "".join(e["v"] for e in events if e["t"] == "matn") == "Salom, dunyo!"
    assert events[-1]["sabab"] == "end_turn"
    assert events[-1]["usage"] == {"kirish": 100, "kesh_oqildi": 50, "kesh_yozildi": 10, "chiqish": 20}

    req, headers = fake_api.requests[0], fake_api.headers[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert req["output_config"] == {"effort": agent.settings.effort}
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["fallbacks"] == "default"
    assert FALLBACK_BETA in headers["anthropic-beta"]
    assert req["stream"] is True
    names = [t["name"] for t in req["tools"]]
    assert names[-1] == "web_search" and "kod_qidir" in names
    assert "tool_choice" not in req

    assert [m["role"] for m in s.messages] == ["user", "assistant"]
    # thinking bloki imzosi bilan tarixda saqlanadi
    assert s.messages[1]["content"][0].type == "thinking"
    assert s.messages[1]["content"][0].signature == "sig123"


def test_no_fallback_param_when_disabled(fake_api, bilim_dir):
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    agent = make_agent(fake_api, bilim_dir)
    agent.settings = dataclasses.replace(agent.settings, zaxira_model="")
    run(collect(agent.javob(Suhbat(id="s"), user("x"))))
    assert "fallbacks" not in fake_api.requests[0]
    assert FALLBACK_BETA not in fake_api.headers[0].get("anthropic-beta", "")


def test_tool_loop(fake_api, bilim_dir):
    fake_api.add(message([
        {"type": "text", "text": "Kodni qidiraman."},
        {"type": "tool_use", "id": "toolu_1", "name": "kod_qidir", "input": {"sozlar": ["suyuq sovun"], "n": 2}},
    ], stop_reason="tool_use"))
    fake_api.add(message([{"type": "text", "text": "Kod: 20.41.31.130-00002"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("Suyuq sovun kodi?"))))

    tool_ev = next(e for e in events if e["t"] == "vosita")
    assert tool_ev["nom"] == "kod_qidir" and tool_ev["kirish"] == {"sozlar": ["suyuq sovun"], "n": 2}
    res_ev = next(e for e in events if e["t"] == "natija")
    assert not res_ev["xato"] and "20.41.31.130-00002" in res_ev["qisqa"]
    assert events[-1]["t"] == "tugadi"

    # ikkinchi so'rovda assistent tool_use va mos tool_result bor
    second = fake_api.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"][1]["type"] == "tool_use"
    tr = second[2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "toolu_1"
    assert "Pastasimon suyuq sovun" in tr["content"] and "is_error" not in tr
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user", "assistant"]


def test_tool_error_is_reported(fake_api, bilim_dir):
    fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "bilim_oqish", "input": {"yol": "../maxfiy.md"}}],
                         stop_reason="tool_use"))
    fake_api.add(message([{"type": "text", "text": "Faylni o'qib bo'lmadi."}]))
    agent = make_agent(fake_api, bilim_dir)
    events = run(collect(agent.javob(Suhbat(id="s"), user("o'qi"))))
    assert next(e for e in events if e["t"] == "natija")["xato"] is True
    assert fake_api.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_refusal_rolls_back(fake_api, bilim_dir):
    fake_api.add(message([{"type": "text", "text": "Avval"}]))
    fake_api.add(message([], stop_reason="refusal",
                         stop_details={"type": "refusal", "category": "cyber", "explanation": "x"}))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    run(collect(agent.javob(s, user("birinchi"))))
    events = run(collect(agent.javob(s, user("ikkinchi"))))
    assert events[-1]["t"] == "xato" and "rad etildi" in events[-1]["v"]
    assert len(s.messages) == 2  # rad etilgan navbat tarixga kirmadi


@pytest.mark.parametrize("status,needle", [(500, "vaqtincha"), (401, "API kaliti"), (429, "chegarasiga"),
                                           (400, "API xatosi (400)"), (404, "Model topilmadi")])
def test_api_errors_roll_back(fake_api, bilim_dir, status, needle):
    fake_api.add(httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "bad"}}))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "xato" and needle in events[-1]["v"]
    assert s.messages == []


def test_error_after_tool_use_leaves_valid_history(fake_api, bilim_dir):
    fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "bilim_royxati", "input": {}}], stop_reason="tool_use"))
    fake_api.add(httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}}))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    run(collect(agent.javob(s, user("x"))))
    assert s.messages == []  # yarim qolgan tool_use tarixda qolmaydi


def test_pause_turn_continues(fake_api, bilim_dir):
    fake_api.add(message([{"type": "text", "text": "Qidiryapman"}], stop_reason="pause_turn"))
    fake_api.add(message([{"type": "text", "text": "Tayyor"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "tugadi"
    assert [m["role"] for m in fake_api.requests[1]["messages"]] == ["user", "assistant"]
    assert [m["role"] for m in s.messages] == ["user", "assistant", "assistant"]


def test_max_steps(fake_api, bilim_dir):
    for i in range(2):
        fake_api.add(message([{"type": "tool_use", "id": f"t{i}", "name": "bilim_royxati", "input": {}}],
                             stop_reason="tool_use"))
    agent = make_agent(fake_api, bilim_dir, max_steps=2)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1] == {"t": "tugadi", "sabab": "qadam_chegarasi", "usage": events[-1]["usage"]}
    assert s.messages[-1]["role"] == "user" and s.messages[-1]["content"][0]["type"] == "tool_result"


def test_max_tokens_with_tool_use_rolls_back(fake_api, bilim_dir):
    fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "bilim_royxati", "input": {}}],
                         stop_reason="max_tokens"))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "xato" and s.messages == []


def test_unparseable_tool_json_is_retried(fake_api, bilim_dir):
    # SDK umuman o'qiy olmagan JSON — navbat qayta so'raladi, vosita ishga tushmaydi
    fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "kod_qidir", "input": {},
                           "raw_json": "not json"}], stop_reason="tool_use"))
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert [e["t"] for e in events if e["t"] in ("qayta", "vosita", "tugadi")] == ["qayta", "tugadi"]
    assert len(fake_api.requests) == 2
    assert [m["role"] for m in s.messages] == ["user", "assistant"]


def test_unparseable_tool_json_gives_up(fake_api, bilim_dir):
    for _ in range(3):
        fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "kod_qidir", "input": {},
                               "raw_json": "not json"}], stop_reason="tool_use"))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "xato" and s.messages == []


def test_cancel_mid_turn_rolls_back(fake_api, bilim_dir):
    fake_api.add(message([{"type": "tool_use", "id": "t1", "name": "bilim_royxati", "input": {}}], stop_reason="tool_use"))
    fake_api.add(message([{"type": "text", "text": "keyin"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")

    async def go():
        agen = agent.javob(s, user("x"))
        async for ev in agen:
            if ev["t"] == "vosita":
                break  # mijoz uzildi
        await agen.aclose()

    run(go())
    assert s.messages == []


def test_web_search_events():
    ev = Agent._stream_event(NS(type="content_block_stop", content_block=NS(
        type="server_tool_use", name="web_search", input={"query": "VMQ-480 lex.uz"})))
    assert ev == {"t": "qidiruv", "v": "VMQ-480 lex.uz"}
    ev = Agent._stream_event(NS(type="content_block_stop", content_block=NS(
        type="web_search_tool_result",
        content=[NS(type="web_search_result", title="lex.uz", url="https://lex.uz/docs/1")])))
    assert ev == {"t": "manbalar", "v": [{"nom": "lex.uz", "url": "https://lex.uz/docs/1"}]}
    ev = Agent._stream_event(NS(type="content_block_stop", content_block=NS(
        type="web_search_tool_result", content=NS(type="web_search_tool_result_error", error_code="max_uses_exceeded"))))
    assert ev == {"t": "manbalar", "v": []}


def test_history_content_without_fallback_is_unchanged():
    blocks = [NS(type="thinking"), NS(type="text"), NS(type="tool_use", id="a")]
    assert history_content(blocks) == blocks


def test_history_content_after_mid_output_fallback():
    blocks = [
        NS(type="thinking"),
        NS(type="text", text="qisman"),
        NS(type="server_tool_use", id="s1"),
        NS(type="web_search_tool_result", tool_use_id="s1"),
        NS(type="server_tool_use", id="s2"),  # natijasiz
        NS(type="tool_use", id="t1"),
        NS(type="fallback"),
        NS(type="thinking", thinking="yangi"),
        NS(type="text", text="davomi"),
        NS(type="tool_use", id="t2"),
    ]
    kept = history_content(blocks)
    assert [(b.type, getattr(b, "id", getattr(b, "text", getattr(b, "thinking", "")))) for b in kept] == [
        ("text", "qisman"), ("server_tool_use", "s1"), ("web_search_tool_result", ""),
        ("thinking", "yangi"), ("text", "davomi"), ("tool_use", "t2"),
    ]


def test_fallback_block_in_stream(fake_api, bilim_dir):
    fb = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"},
          "trigger": {"type": "refusal", "category": "cyber"}}
    fake_api.add(message([fb, {"type": "text", "text": "Javob"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    z = next(e for e in events if e["t"] == "zaxira")
    assert z == {"t": "zaxira", "dan": "claude-opus-5-5", "ga": "claude-opus-4-8"}
    assert [b.type for b in s.messages[1]["content"]] == ["text"]


def test_ombor_eviction_skips_locked():
    async def go():
        om = SuhbatOmbori(2)
        a = om.ol("a")
        await a.lock.acquire()
        om.ol("b")
        om.ol("c")
        assert om.topish("a") is a  # band suhbat chiqarib yuborilmadi
        assert om.topish("b") is None
        assert om.ol("a") is a
        assert len(om.ol(None).id) == 32
        assert om.ol("x" * 65).id != "x" * 65
    run(go())
