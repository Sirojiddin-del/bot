import asyncio
import dataclasses
from types import SimpleNamespace as NS

import httpx2
import pytest

from app.agent import FALLBACK_BETA, MAX_PAUSES, Agent, Suhbat, SuhbatOmbori, build_system, history_content, rasm_hisobi
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
    assert "Suhbat boshlangan sana: 2026-10-22" in s
    assert "# Davlat xaridlari agenti (test)" in s
    assert "kod_qidir" in s


def test_simple_turn_request_shape(fake_api, bilim_dir):
    fake_api.add(message([{"type": "thinking", "thinking": "o'ylayman"}, {"type": "text", "text": "Salom, dunyo!"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s1")
    events = run(collect(agent.javob(s, user("Salom"))))

    types = [e["t"] for e in events]
    assert types[0] == "qadam" and types.count("qadam") == 1
    assert "fikr_blok" in types and "fikr" in types and types[-1] == "tugadi"
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
    assert [e["t"] for e in events[-2:]] == ["bekor", "xato"] and "rad etildi" in events[-1]["v"]
    assert len(s.messages) == 2  # rad etilgan navbat tarixga kirmadi


@pytest.mark.parametrize("status,needle", [(500, "vaqtincha"), (401, "API kaliti"), (429, "chegarasiga"),
                                           (400, "API xatosi (400)"), (404, "Model topilmadi"),
                                           (413, "Yangi suhbat boshlang")])
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
    fake_api.add(message([{"type": "text", "text": "davomi"}]))
    agent = make_agent(fake_api, bilim_dir, max_steps=2)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "tugadi" and events[-1]["sabab"] == "qadam_chegarasi"
    assert len(fake_api.requests) == 2
    assert s.messages[-1]["role"] == "user" and s.messages[-1]["content"][0]["type"] == "tool_result"
    # "davom et" — tool_result dan keyin yana bir user xabari; API ularni bitta navbatga birlashtiradi
    events = run(collect(agent.javob(s, user("davom et"))))
    assert events[-1]["sabab"] == "end_turn"
    assert [m["role"] for m in fake_api.requests[2]["messages"]] == ["user", "assistant", "user", "assistant", "user", "user"]


def test_pause_turn_does_not_count_as_step_and_is_bounded(fake_api, bilim_dir):
    # qadam chegarasi 1 bo'lsa ham to'xtatilgan (pause_turn) javob davom ettiriladi
    fake_api.add(message([{"type": "text", "text": "Qidiryapman"}], stop_reason="pause_turn"))
    fake_api.add(message([{"type": "text", "text": "Tayyor"}]))
    agent = make_agent(fake_api, bilim_dir, max_steps=1)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "tugadi" and events[-1]["sabab"] == "end_turn"
    # cheksiz pause_turn — xato va tarix tiklanadi (tarixda yarim qolgan server_tool_use qolmaydi)
    for _ in range(MAX_PAUSES + 1):
        fake_api.add(message([{"type": "text", "text": "…"}], stop_reason="pause_turn"))
    s2 = Suhbat(id="s2")
    events = run(collect(agent.javob(s2, user("x"))))
    assert events[-1]["t"] == "xato" and s2.messages == []


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


def test_tool_use_before_mid_stream_fallback_is_not_executed(fake_api, bilim_dir):
    """Zaxira model ulanishidan oldingi tool_use bajarilmaydi va tarixga tushmaydi."""
    fb = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"},
          "trigger": {"type": "refusal", "category": "cyber"}}
    # 1) zaxira model matn bilan yakunladi
    fake_api.add(message([
        {"type": "thinking", "thinking": "..."},
        {"type": "tool_use", "id": "toolu_rad", "name": "bilim_royxati", "input": {}},
        fb,
        {"type": "text", "text": "Zaxira javobi"},
    ]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    events = run(collect(agent.javob(s, user("x"))))
    assert events[-1]["t"] == "tugadi" and not any(e["t"] == "vosita" for e in events)
    assert len(fake_api.requests) == 1
    assert [b.type for b in s.messages[1]["content"]] == ["text"]

    # 2) zaxira model o'zi ham vosita chaqirdi — faqat uning chaqiruvi bajariladi
    fake_api.add(message([
        {"type": "tool_use", "id": "toolu_rad", "name": "bilim_royxati", "input": {}},
        fb,
        {"type": "tool_use", "id": "toolu_zaxira", "name": "bilim_royxati", "input": {}},
    ], stop_reason="tool_use"))
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    s2 = Suhbat(id="s2")
    events = run(collect(agent.javob(s2, user("x"))))
    assert [e["id"] for e in events if e["t"] == "vosita"] == ["toolu_zaxira"]
    sent = fake_api.requests[-1]["messages"]
    assert [b["id"] for b in sent[1]["content"] if b["type"] == "tool_use"] == ["toolu_zaxira"]
    assert [b["tool_use_id"] for b in sent[2]["content"]] == ["toolu_zaxira"]


def test_system_prompt_is_stable_across_turns_and_days(fake_api, bilim_dir, monkeypatch):
    import datetime as dt
    import app.agent as agent_mod

    class Kun(dt.date):
        bugun = dt.date(2026, 11, 12)

        @classmethod
        def today(cls):
            return cls.bugun

    monkeypatch.setattr(agent_mod.dt, "date", Kun)
    for _ in range(3):
        fake_api.add(message([{"type": "thinking", "thinking": "o'y"}, {"type": "text", "text": "ok"}]))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    run(collect(agent.javob(s, user("bir"))))
    # bilim bazasi yangilandi va sana o'zgardi — davom etayotgan suhbatning tizim matni o'zgarmasligi kerak
    (bilim_dir / "SKILL.md").write_text("# Butunlay yangi matn", encoding="utf-8")
    run(collect(agent.javob(s, user("ikki"))))
    Kun.bugun = dt.date(2026, 11, 13)
    run(collect(agent.javob(s, user("uch"))))
    systems = [r["system"][0]["text"] for r in fake_api.requests]
    assert systems[0] == systems[1] == systems[2]
    assert "Suhbat boshlangan sana: 2026-11-12" in systems[0]
    # yangi sana xabar ichida aytiladi; oldingi xabarlar (thinking bloklari bilan) o'zgarmagan
    last_user = fake_api.requests[2]["messages"][-1]["content"]
    assert last_user[0]["text"] == "[Tizim eslatmasi: bugungi sana — 2026-11-13.]" and last_user[1]["text"] == "uch"
    assert fake_api.requests[2]["messages"][:3] == fake_api.requests[1]["messages"][:3]
    assert fake_api.requests[1]["messages"][2]["content"] == [{"type": "text", "text": "ikki"}]
    # yangi suhbat yangi bilim bazasini oladi
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    run(collect(agent.javob(Suhbat(id="yangi"), user("x"))))
    assert "Butunlay yangi matn" in fake_api.requests[3]["system"][0]["text"]


def test_usage_counts_declined_attempt_when_fallback_served(fake_api, bilim_dir):
    usage = {"input_tokens": 100, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    events = message([{"type": "text", "text": "ok"}], usage=usage)
    events[-2]["usage"] = {
        "output_tokens": 30,
        "iterations": [
            {"type": "message", "input_tokens": 1000, "output_tokens": 200,
             "cache_read_input_tokens": 5, "cache_creation_input_tokens": 7},
            {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 100, "output_tokens": 30,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        ],
    }
    fake_api.add(events)
    agent = make_agent(fake_api, bilim_dir)
    out = run(collect(agent.javob(Suhbat(id="s"), user("x"))))
    assert out[-1]["usage"] == {"kirish": 1100, "kesh_oqildi": 5, "kesh_yozildi": 7, "chiqish": 230}


def test_rasm_hisobi_and_session_counters(fake_api, bilim_dir):
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 1000}}
    assert rasm_hisobi([{"role": "user", "content": [img, img, {"type": "text", "text": "x"}]},
                        {"role": "assistant", "content": "matn"}]) == (2, 2000)
    fake_api.add(message([{"type": "text", "text": "ok"}]))
    fake_api.add(httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}}))
    agent = make_agent(fake_api, bilim_dir)
    s = Suhbat(id="s")
    run(collect(agent.javob(s, [img, {"type": "text", "text": "x"}])))
    assert (s.rasm_soni, s.rasm_hajmi) == (1, 1000)
    run(collect(agent.javob(s, [img, {"type": "text", "text": "y"}])))  # xato — tiklanadi
    assert (s.rasm_soni, s.rasm_hajmi) == (1, 1000)


def test_ombor_basic():
    om = SuhbatOmbori(10)
    a = om.yarat()
    assert len(a.id) == 32 and om.topish(a.id) is a
    assert om.topish("yoq") is None and om.topish(None) is None and om.topish("") is None
    om.ochir(a.id)
    assert om.topish(a.id) is None and len(om) == 0


def test_ombor_evicts_oldest_but_never_locked_or_new():
    async def go():
        om = SuhbatOmbori(2)
        a, b = om.yarat(), om.yarat()
        await a.lock.acquire()
        c = om.yarat()
        assert om.topish(a.id) is a  # band suhbat chiqarib yuborilmadi
        assert om.topish(b.id) is None and om.topish(c.id) is c
        # hammasi band bo'lsa, yangi yaratilgan suhbat baribir saqlanadi (chegaradan vaqtincha oshadi)
        await c.lock.acquire()
        d = om.yarat()
        assert om.topish(d.id) is d and len(om) == 3
    run(go())


def test_ombor_lru_order_follows_use():
    om = SuhbatOmbori(2)
    a, b = om.yarat(), om.yarat()
    om.topish(a.id)  # a yaqinda ishlatildi
    om.yarat()
    assert om.topish(a.id) is a and om.topish(b.id) is None


def test_ombor_ttl_and_image_budget():
    om = SuhbatOmbori(100, ttl=60, max_rasm_hajmi=1000)
    eski, a, b = om.yarat(), om.yarat(), om.yarat()
    eski.yangilangan -= 3600
    a.rasm_hajmi = b.rasm_hajmi = 600
    c = om.yarat()
    assert om.topish(eski.id) is None  # muddati o'tgan
    assert om.topish(a.id) is None  # rasm hajmi chegarasi: eng eskisi chiqarildi
    assert om.topish(b.id) is b and om.topish(c.id) is c
