import pytest

from app.bilim import Bilim, BilimError, norm, to_latin


def test_norm_and_translit():
    assert norm("Qog‘oz  SOCHIQ") == "qog'oz sochiq"
    assert to_latin("ўртача бозор нархи") == "o'rtacha bozor narxi"
    assert to_latin("кондиционер") == "konditsioner"


def test_mavjud_and_skill(bilim_dir):
    b = Bilim(bilim_dir)
    assert b.mavjud()
    assert "Davlat xaridlari agenti" in b.skill_matni()
    assert not Bilim(bilim_dir / "yoq").mavjud()


def test_royxat_lists_files_but_not_classifier(bilim_dir):
    out = Bilim(bilim_dir).royxat()
    assert "laws/11_vmq480.md" in out
    assert "VMQ-480 (test)" in out
    assert "data/klassifikator.json  (" not in out
    assert "faqat kod_qidir" in out


def test_oqish_paging(bilim_dir):
    b = Bilim(bilim_dir)
    out = b.oqish("templates/katta.md", 10, 5)
    assert out.splitlines()[0] == "templates/katta.md — 10–14-qatorlar (jami 1000)"
    assert "10\tqator 10" in out
    assert "boshlanish=15" in out
    last = b.oqish("templates/katta.md", 998, 50)
    assert "davomi bor" not in last


@pytest.mark.parametrize("path", ["../maxfiy.md", "/etc/passwd", "laws/../../maxfiy.md"])
def test_oqish_blocks_traversal(bilim_dir, path):
    with pytest.raises(BilimError):
        Bilim(bilim_dir).oqish(path)


def test_oqish_errors(bilim_dir):
    b = Bilim(bilim_dir)
    with pytest.raises(BilimError, match="kod_qidir"):
        b.oqish("data/klassifikator.json")
    with pytest.raises(BilimError, match="topilmadi"):
        b.oqish("laws/yoq.md")
    with pytest.raises(BilimError, match="juda katta"):
        b.oqish("laws/11_vmq480.md", 999)


def test_qidirish_latin_finds_cyrillic(bilim_dir):
    out = Bilim(bilim_dir).qidirish("o'rtacha bozor narxi")
    assert "laws/11_vmq480.md:4:" in out
    assert "SKILL.md" in out


def test_qidirish_russian_and_all_words_fallback(bilim_dir):
    b = Bilim(bilim_dir)
    assert "references/usullar.md:5:" in b.qidirish("прямой договор")
    out = b.qidirish("xarid kichik")  # ibora yo'q, lekin ikkala so'z bir qatorda bor
    assert "barcha so'zlar" in out and "references/usullar.md:3:" in out


def test_qidirish_folder_and_errors(bilim_dir):
    b = Bilim(bilim_dir)
    assert "topilmadi" in b.qidirish("narx", papka="templates")
    with pytest.raises(BilimError):
        b.qidirish("narx", papka="data")
    with pytest.raises(BilimError):
        b.qidirish("a")


def test_klassifikator_search(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    assert len(k) == 7 and k.holat == "2026-09-18"
    top = [item[0] for _, item in k.qidir("suyuq sovun", 3)]
    assert top[0] == "20.41.31.130-00002"
    assert "20.11.11.140-00002" not in top[:2]
    # ruscha va o'zbekcha kirill so'rov
    assert k.qidir("мыло жидкое", 1)[0][1][0].startswith("20.41.31.130")
    # bosh so'z: "maishiy konditsioner" filtrdan yuqori
    assert k.qidir("konditsioner", 2)[0][1][0] == "28.25.12.130-00001"
    assert k.qidir("кондиционер", 1)[0][1][0] == "28.25.12.130-00001"
    assert k.qidir("zzzzzz") == []


def test_klassifikator_prefix(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    assert [r[0] for r in k.prefiks("20.41")] == ["20.41.31.130-00002", "20.41.31.130-00001"]


def test_klassifikator_missing(tmp_path):
    (tmp_path / "SKILL.md").write_text("x")
    with pytest.raises(BilimError):
        Bilim(tmp_path).klassifikator
