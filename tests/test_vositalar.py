import math

import pytest

from app.bilim import Bilim
from app.vositalar import TOOL_DEFS, WEB_SEARCH_TOOL, run_tool, tool_params


@pytest.fixture
def bilim(bilim_dir):
    return Bilim(bilim_dir)


def test_tool_params_order_and_flags():
    tools = tool_params(web_search=True)
    assert [t["name"] for t in tools] == [t["name"] for t in TOOL_DEFS] + ["web_search"]
    assert all(t.get("eager_input_streaming") for t in tools[:-1])
    assert tools[-1] == WEB_SEARCH_TOOL
    assert [t["name"] for t in tool_params(web_search=False)] == [t["name"] for t in TOOL_DEFS]
    # ro'yxat har safar bir xil — kesh prefiksi barqaror
    assert tool_params(True) == tool_params(True)


def test_narx_tahlili_numbers(bilim):
    args = {"manbalar": [{"nomi": "A", "narx": 185000}, {"nomi": "B", "narx": 172000, "hajm": 40},
                         {"nomi": "C", "narx": "178 000", "hajm": 30}], "miqdor": 40}
    out, err = run_tool(bilim, "narx_tahlili", args)
    assert not err
    obn = (185000 * 1 + 172000 * 40 + 178000 * 30) / 71
    sigma = math.sqrt(sum((x - obn) ** 2 for x in (185000, 172000, 178000)) / 2)
    assert f"{obn:,.2f}".replace(",", " ") in out
    assert f"{sigma / obn * 100:.1f}%" in out
    assert f"{obn * 40:,.2f}".replace(",", " ") in out
    assert f"{0.8 * obn * 40:,.2f}".replace(",", " ") in out
    assert "OK (≤30%)" in out
    assert "3 tadan kam" not in out


def test_narx_tahlili_warnings(bilim):
    out, err = run_tool(bilim, "narx_tahlili", {"manbalar": [{"nomi": "A", "narx": 100}, {"nomi": "B", "narx": 300}],
                                                "miqdor": 1, "tafovut": 15})
    assert not err
    assert "3 tadan kam" in out and ">30%" in out and "t > 10%" in out


@pytest.mark.parametrize("args", [
    {"manbalar": [], "miqdor": 1},
    {"manbalar": [{"nomi": "A", "narx": "abc"}], "miqdor": 1},
    {"manbalar": [{"nomi": "A", "narx": -5}], "miqdor": 1},
    {"manbalar": [{"nomi": "A", "narx": 5, "hajm": 0}], "miqdor": 1},
    {"manbalar": [{"nomi": "A", "narx": 5}], "miqdor": 0},
    {"manbalar": [{"nomi": "A", "narx": 5}]},
    {"manbalar": [{"nomi": "A", "narx": float("inf")}], "miqdor": 1},
    {"manbalar": ["A=5"], "miqdor": 1},
    {"manbalar": [{"nomi": "A", "narx": True}], "miqdor": 1},
])
def test_narx_tahlili_rejects_bad_input(bilim, args):
    out, err = run_tool(bilim, "narx_tahlili", args)
    assert err and out.startswith("Xato:")


def test_kod_qidir(bilim):
    out, err = run_tool(bilim, "kod_qidir", {"sozlar": ["suyuq sovun", "choyshab"], "n": 2})
    assert not err
    assert "20.41.31.130-00002 | Pastasimon suyuq sovun | Мыло жидкое пастообразное" in out
    assert "13.92.12.119-00001 | Choyshab" in out
    out, err = run_tool(bilim, "kod_qidir", {"kod": "28.25"})
    assert not err and "28.25.12.130-00001" in out and "1 ta" in out


@pytest.mark.parametrize("args", [{}, {"sozlar": "sovun"}, {"sozlar": [1, 2]}, {"sozlar": ["  "]}])
def test_kod_qidir_rejects_bad_input(bilim, args):
    out, err = run_tool(bilim, "kod_qidir", args)
    assert err


def test_bilim_tools(bilim):
    out, err = run_tool(bilim, "bilim_royxati", {})
    assert not err and "references/usullar.md" in out
    out, err = run_tool(bilim, "bilim_oqish", {"yol": "laws/11_vmq480.md", "boshlanish": 3, "qatorlar": 1})
    assert not err and "3\t30. Ўзгариш" in out
    out, err = run_tool(bilim, "bilim_oqish", {"yol": "../maxfiy.md"})
    assert err
    out, err = run_tool(bilim, "bilim_qidirish", {"soz": "boshlang'ich narx", "papka": "laws"})
    assert not err and "laws/11_vmq480.md:5:" in out
    out, err = run_tool(bilim, "bilim_oqish", {"yol": "laws/11_vmq480.md", "boshlanish": "x"})
    assert err


def test_run_tool_guards(bilim):
    assert run_tool(bilim, "yoq_vosita", {}) == ("Noma'lum vosita: yoq_vosita", True)
    out, err = run_tool(bilim, "kod_qidir", ["sovun"])
    assert err and "JSON obyekt" in out


def test_run_tool_unexpected_exception(bilim, monkeypatch):
    from app import vositalar

    def boom(_b, _a):
        raise RuntimeError("portladi")

    monkeypatch.setitem(vositalar.HANDLERS, "bilim_royxati", boom)
    out, err = run_tool(bilim, "bilim_royxati", {})
    assert err and "RuntimeError" in out
