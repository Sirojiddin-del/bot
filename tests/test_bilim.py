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


def codes(k, query, n=3):
    return [item[0] for _, item in k.qidir(query, n)]


def test_klassifikator_search(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    assert len(k) == 15 and k.holat == "2026-09-18"
    top = codes(k, "suyuq sovun")
    assert top[0] == "20.41.31.130-00002"
    assert "20.11.11.140-00002" not in top[:2]
    # ruscha va o'zbekcha kirill so'rov
    assert codes(k, "мыло жидкое", 1)[0].startswith("20.41.31.130")
    assert codes(k, "суюқ совун", 1) == ["20.41.31.130-00002"]
    assert k.qidir("zzzzzz") == []


def test_klassifikator_head_noun_beats_modifier(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    # o'zbekchada bosh so'z oxirida: "maishiy konditsioner" — konditsioner, "konditsioner filtri" — filtr
    assert codes(k, "konditsioner", 2)[0] == "28.25.12.130-00001"
    assert codes(k, "кондиционер", 1) == ["28.25.12.130-00001"]
    # "non idish" (non uchun idish) haqiqiy nondan pastda
    assert codes(k, "non", 2) == ["10.71.11.110-00001", "23.41.12.110-00002"]


def test_klassifikator_uzbek_suffixes(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    # egalik qo'shimchasi: sut -> "ichimlik suti" zardobdan yuqori
    top = codes(k, "sut")
    assert top[0] == "10.51.51.000-00001" and top[1] == "10.51.11.000-00001"
    assert top.index("10.51.11.000-00001") < top.index("10.51.55.111-00001")
    # so'rovning o'zi qo'shimchali: "ofis qog'ozi" -> "... oq qog'oz"; sochiq -> sochig'i almashuvi buzilmagan
    assert codes(k, "ofis qog'ozi", 1) == ["17.12.14.129-00002"]
    assert codes(k, "sochiq", 1) == ["17.22.11.130-00003"]


def test_klassifikator_short_tokens_and_partial_fallback(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    # "ai" va "92" hech qayerda so'z emas ("benzocAIne" ichidagi "ai" hisobga olinmaydi):
    # to'liq mos pozitsiya yo'q, shuning uchun qisman mos kelgan benzin chiqadi
    assert codes(k, "benzin AI-92") == ["19.20.21.100-00001"]


def test_klassifikator_query_is_capped(bilim_dir):
    from app.bilim import MAX_QUERY_CHARS, MAX_QUERY_TOKENS, _Sorov
    k = Bilim(bilim_dir).klassifikator
    long_query = "suyuq sovun " + " ".join(f"soz{i}" for i in range(2000))
    # faqat birinchi 100 belgi va 8 ta so'z hisobga olinadi
    assert k.qidir(long_query) == k.qidir(long_query[:MAX_QUERY_CHARS])
    assert len(_Sorov(long_query, uzbek=True).toks) == MAX_QUERY_TOKENS


def test_klassifikator_prefix(bilim_dir):
    k = Bilim(bilim_dir).klassifikator
    assert [r[0] for r in k.prefiks("20.41")] == ["20.41.31.130-00002", "20.41.31.130-00001"]


def test_klassifikator_missing(tmp_path):
    (tmp_path / "SKILL.md").write_text("x")
    with pytest.raises(BilimError):
        Bilim(tmp_path).klassifikator


def test_symlinks_cannot_leave_knowledge_base(bilim_dir, tmp_path):
    secret = tmp_path / "tashqarida.txt"
    secret.write_text("MAXFIY_KALIT=sk-123", encoding="utf-8")
    (bilim_dir / "references" / "havola.md").symlink_to(secret)
    outside_dir = tmp_path / "tashqi_papka"
    outside_dir.mkdir()
    (outside_dir / "ichki.md").write_text("MAXFIY_KALIT ikkinchi", encoding="utf-8")
    (bilim_dir / "templates" / "papka").symlink_to(outside_dir, target_is_directory=True)
    b = Bilim(bilim_dir)
    assert "havola.md" not in b.royxat() and "ichki.md" not in b.royxat()
    assert "topilmadi" in b.qidirish("MAXFIY_KALIT")
    for path in ("references/havola.md", "templates/papka/ichki.md"):
        with pytest.raises(BilimError):
            b.oqish(path)


def test_search_cache_follows_file_changes(bilim_dir):
    import os
    b = Bilim(bilim_dir)
    assert "topilmadi" in b.qidirish("yangiqoida")
    f = bilim_dir / "references" / "usullar.md"
    f.write_text(f.read_text(encoding="utf-8") + "\nYangiqoida: tender.\n", encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 5_000_000_000))
    assert "references/usullar.md:7: Yangiqoida: tender." in b.qidirish("yangiqoida")


def test_oqish_truncates_huge_line(bilim_dir):
    (bilim_dir / "references" / "uzun.json").write_text("{" + "x" * 200_000 + "}", encoding="utf-8")
    out = Bilim(bilim_dir).oqish("references/uzun.json")
    assert len(out) < 25_000 and "qator qisqartirildi: jami 200002 belgi" in out


def test_qidirish_rejects_very_long_query(bilim_dir):
    with pytest.raises(BilimError, match="juda uzun"):
        Bilim(bilim_dir).qidirish("narx " * 100)
