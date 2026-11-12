import importlib.util
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.bilim import Bilim, BilimError

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bilim_yukla.py"
spec = importlib.util.spec_from_file_location("bilim_yukla", SCRIPT)
by = importlib.util.module_from_spec(spec)
spec.loader.exec_module(by)

SHABLON = "# Tashkilot profili\n- buyurtmachi_turi: [byudjet | korporativ]\n- STIR: [ ]\n"


@pytest.fixture
def skill(tmp_path):
    root = tmp_path / "manba" / "davlat-xarid-agent"
    (root / "profil").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "laws").mkdir()
    (root / "__pycache__").mkdir()
    (root / "SKILL.md").write_text("# Skill v1", encoding="utf-8")
    (root / "profil" / "tashkilot_profili.md").write_text(SHABLON, encoding="utf-8")
    (root / "data" / "klassifikator.json").write_text('{"items": []}', encoding="utf-8")
    (root / "laws" / "01.md").write_text("# Qonun", encoding="utf-8")
    (root / "__pycache__" / "x.pyc").write_bytes(b"x")
    return root


def test_loads_folder_and_reports(skill, tmp_path):
    target = tmp_path / "ish" / "bilim"
    natija = by.yukla(skill, target)
    assert natija == {"fayllar": 5, "klassifikator": True, "profil_saqlandi": False}  # 4 fayl + qayd
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# Skill v1"
    assert not (target / "__pycache__").exists()
    # xizmat qaydi agent vositalariga ko'rinmaydi
    b = Bilim(target)
    assert ".manba.json" not in b.royxat()
    with pytest.raises(BilimError):
        b.oqish(".manba.json")
    # vaqtinchalik papkalar qolmagan
    assert sorted(p.name for p in target.parent.iterdir()) == ["bilim"]


def test_update_replaces_files_but_keeps_filled_profile(skill, tmp_path):
    target = tmp_path / "bilim"
    by.yukla(skill, target)
    (target / "eski_fayl.md").write_text("eski", encoding="utf-8")
    toldirilgan = SHABLON.replace("STIR: [ ]", "STIR: 123456789")
    (target / "profil" / "tashkilot_profili.md").write_text(toldirilgan, encoding="utf-8")
    # skill yangilandi: yangi matn va profil shabloniga yangi maydon
    (skill / "SKILL.md").write_text("# Skill v2", encoding="utf-8")
    yangi_shablon = SHABLON + "- yangi_maydon: [ ]\n"
    (skill / "profil" / "tashkilot_profili.md").write_text(yangi_shablon, encoding="utf-8")

    natija = by.yukla(skill, target)
    assert natija["profil_saqlandi"] is True
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# Skill v2"
    assert not (target / "eski_fayl.md").exists()
    assert (target / "profil" / "tashkilot_profili.md").read_text(encoding="utf-8") == toldirilgan
    assert (target / "profil" / "tashkilot_profili.md.yangi-shablon").read_text(encoding="utf-8") == yangi_shablon
    assert "yangi-shablon" not in Bilim(target).royxat()
    # uchinchi yuklashda ham to'ldirilgan profil joyida
    assert by.yukla(skill, target)["profil_saqlandi"] is True
    assert "123456789" in (target / "profil" / "tashkilot_profili.md").read_text(encoding="utf-8")


def test_untouched_profile_template_is_updated(skill, tmp_path):
    target = tmp_path / "bilim"
    by.yukla(skill, target)
    yangi_shablon = SHABLON + "- yangi_maydon: [ ]\n"
    (skill / "profil" / "tashkilot_profili.md").write_text(yangi_shablon, encoding="utf-8")
    assert by.yukla(skill, target)["profil_saqlandi"] is False
    assert (target / "profil" / "tashkilot_profili.md").read_text(encoding="utf-8") == yangi_shablon


def test_profile_kept_when_no_marker_from_older_loader(skill, tmp_path):
    target = tmp_path / "bilim"
    by.yukla(skill, target)
    (target / by.BELGI).unlink()
    (target / "profil" / "tashkilot_profili.md").write_text("mening profilim", encoding="utf-8")
    assert by.yukla(skill, target)["profil_saqlandi"] is True
    assert (target / "profil" / "tashkilot_profili.md").read_text(encoding="utf-8") == "mening profilim"


def test_refuses_dangerous_targets(skill, tmp_path, monkeypatch):
    loyiha = tmp_path / "loyiha"
    (loyiha / "ichki").mkdir(parents=True)
    (loyiha / ".env").write_text("ANTHROPIC_API_KEY=sk", encoding="utf-8")
    monkeypatch.chdir(loyiha / "ichki")
    for xavfli in (Path("."), Path(".."), loyiha, tmp_path, Path.home(), by.ROOT, by.ROOT.parent, Path("/")):
        with pytest.raises(by.YuklashXatosi):
            by.yukla(skill, xavfli, majburan=True)
    assert (loyiha / ".env").exists()


def test_refuses_to_wipe_unrelated_directory_without_force(skill, tmp_path):
    hujjatlar = tmp_path / "hujjatlar"
    hujjatlar.mkdir()
    (hujjatlar / "muhim.docx").write_text("muhim", encoding="utf-8")
    with pytest.raises(by.YuklashXatosi, match="--majburan"):
        by.yukla(skill, hujjatlar)
    assert (hujjatlar / "muhim.docx").exists()
    by.yukla(skill, hujjatlar, majburan=True)
    assert (hujjatlar / "SKILL.md").exists() and not (hujjatlar / "muhim.docx").exists()
    # bo'sh papka va mavjud bo'lmagan papka — muammosiz
    (tmp_path / "bosh").mkdir()
    by.yukla(skill, tmp_path / "bosh")
    by.yukla(skill, tmp_path / "yangi" / "ichida")


def test_refuses_symlink_target_and_overlap(skill, tmp_path):
    haqiqiy = tmp_path / "haqiqiy"
    haqiqiy.mkdir()
    (haqiqiy / "SKILL.md").write_text("x", encoding="utf-8")
    havola = tmp_path / "havola"
    havola.symlink_to(haqiqiy, target_is_directory=True)
    with pytest.raises(by.YuklashXatosi, match="symlink"):
        by.yukla(skill, havola)
    with pytest.raises(by.YuklashXatosi, match="ustma-ust"):
        by.yukla(skill, skill / "ichida")
    with pytest.raises(by.YuklashXatosi, match="ustma-ust"):
        by.yukla(skill, skill)
    with pytest.raises(by.YuklashXatosi, match="topilmadi"):
        by.yukla(tmp_path / "yoq", tmp_path / "bilim")
    with pytest.raises(by.YuklashXatosi, match="SKILL.md"):
        by.yukla(haqiqiy.parent / "manba" / "davlat-xarid-agent" / "laws", tmp_path / "bilim")


def test_symlinks_in_source_are_not_copied(skill, tmp_path):
    maxfiy = tmp_path / "maxfiy.txt"
    maxfiy.write_text("ANTHROPIC_API_KEY=sk-ant-MAXFIY", encoding="utf-8")
    (skill / "references").mkdir()
    (skill / "references" / "eslatma.md").symlink_to(maxfiy)
    (skill / "tashqi").symlink_to(tmp_path, target_is_directory=True)
    target = tmp_path / "bilim"
    by.yukla(skill, target)
    assert not (target / "references" / "eslatma.md").exists() and not (target / "tashqi").exists()
    assert "MAXFIY" not in "".join(p.read_text(encoding="utf-8", errors="ignore")
                                   for p in target.rglob("*") if p.is_file())


def test_loads_skill_zip_and_rejects_bad_archives(skill, tmp_path):
    arxiv = tmp_path / "agent.skill"
    with zipfile.ZipFile(arxiv, "w") as zf:
        for p in skill.rglob("*"):
            if p.is_file():
                zf.write(p, Path("davlat-xarid-agent") / p.relative_to(skill))
    target = tmp_path / "bilim"
    assert by.yukla(arxiv, target)["klassifikator"] is True
    assert (target / "SKILL.md").is_file()

    yomon = tmp_path / "yomon.skill"
    with zipfile.ZipFile(yomon, "w") as zf:
        zf.writestr("SKILL.md", "x")
        zf.writestr("../../tashqarida.txt", "x")
    with pytest.raises(by.YuklashXatosi, match="xavfli"):
        by.yukla(yomon, tmp_path / "b2")
    assert not (tmp_path.parent / "tashqarida.txt").exists()

    (tmp_path / "matn.skill").write_text("zip emas", encoding="utf-8")
    with pytest.raises(by.YuklashXatosi, match="zip"):
        by.yukla(tmp_path / "matn.skill", tmp_path / "b3")


def test_failed_load_keeps_existing_knowledge_base(skill, tmp_path, monkeypatch):
    target = tmp_path / "bilim"
    by.yukla(skill, target)

    def portla(*a, **kw):
        raise OSError("disk to'ldi")

    monkeypatch.setattr(by.shutil, "copytree", portla)
    with pytest.raises(OSError):
        by.yukla(skill, target)
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "# Skill v1"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".bilim")) == []


def test_cli_messages(skill, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["bilim_yukla.py", str(skill), "--maqsad", str(tmp_path / "bilim")])
    by.main()
    out = capsys.readouterr().out
    assert "Tayyor: 5 ta fayl" in out and "Klassifikator bazasi: bor" in out
    monkeypatch.setattr("sys.argv", ["bilim_yukla.py", str(tmp_path / "yoq")])
    with pytest.raises(SystemExit) as e:
        by.main()
    assert str(e.value).startswith("Xato:")
