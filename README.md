# Davlat xaridlari agenti — veb-ilova

O'zbekiston davlat buyurtmachilari (byudjet va korporativ) uchun Claude asosidagi AI agent. Brauzerda ochiladigan chat orqali ishlaydi: xarid usulini tanlaydi, VMQ-480 bo'yicha narx tahlilini qiladi, klassifikator kodini topadi, raqobatni cheklamaydigan texnik topshiriq va hujjatlar tayyorlaydi, qonun matnlari asosida savollarga javob beradi.

Agentning bilimi va ish qoidalari sizning **"davlat-xarid-agent"** skillingizdan olinadi. Skill mazmuni bu repoga kirmaydi — u serverdagi `bilim/` papkasida turadi (`.gitignore` da).

## Imkoniyatlar

- **Chat sahifasi** — javoblar jonli (oqimli) chiqadi, jadval va ro'yxatlar formatlangan, kod bloklarini bir bosishda nusxalash mumkin.
- **Rasm yuborish** — talabnoma surati yoki platforma ekranini biriktirasiz (katta telefon suratlari avtomatik kichraytiriladi).
- **Agent vositalari:**
  - `kod_qidir` — klassifikator bo'yicha oflayn qidiruv (24 157 pozitsiya; lotin, kirill, rus tilida);
  - `narx_tahlili` — O'BN, variatsiya koeffitsienti, boshlang'ich narx va PF-259 xavf zonasi;
  - `bilim_royxati`, `bilim_oqish`, `bilim_qidirish` — qonunlar, yo'riqnomalar va shablonlarni o'qish va qidirish (lotin va kirill yozuvi birga qidiriladi);
  - `web_search` — lex.uz va ochiq manbalarni tekshirish (o'chirib qo'yish mumkin).
- Agent qaysi vositani ishlatayotgani va "fikrlash jarayoni" chatda ko'rinib turadi.

## O'rnatish

Python 3.10+ kerak.

```bash
git clone https://github.com/Sirojiddin-del/bot.git
cd bot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 1. Bilim bazasini yuklash

Skill papkasidan yoki yuklab olingan `.skill` faylidan:

```bash
python3 scripts/bilim_yukla.py /yol/davlat-xarid-agent
# yoki
python3 scripts/bilim_yukla.py /yol/davlat-xarid-agent.skill
```

Skillni yangilasangiz — shu buyruqni qayta ishga tushiring va serverni qayta yoqing.

Tashkilot profilini oldindan to'ldirmoqchi bo'lsangiz, `bilim/profil/tashkilot_profili.md` faylini tahrirlang — agent uni o'qiydi. Yoki profilni chatda yuboring.

### 2. Sozlash

```bash
cp .env.example .env
```

`.env` faylida kamida `ANTHROPIC_API_KEY` ni yozing. Kalitni [platform.claude.com](https://platform.claude.com) dan olasiz.

### 3. Ishga tushirish

```bash
uvicorn app.server:app --host 127.0.0.1 --port 8000
```

Brauzerda oching: <http://127.0.0.1:8000>

## Sozlamalar (`.env`)

| O'zgaruvchi | Sukut | Ma'nosi |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API kaliti (majburiy) |
| `KIRISH_PAROLI` | bo'sh | Sahifaga kirish paroli. Internetga ochiq serverda **albatta** qo'ying |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model |
| `EFFORT` | `high` | Fikrlash chuqurligi: `low`, `medium`, `high`, `xhigh`, `max` |
| `VEB_QIDIRUV` | `1` | Internetda qidiruv (`0` — o'chirilgan) |
| `ZAXIRA_MODEL` | `default` | Xavfsizlik klassifikatori so'rovni rad etsa, server tomonda zaxira modelga o'tish (`yoq` — o'chirilgan) |
| `BILIM_PAPKASI` | `bilim` | Bilim bazasi papkasi |
| `MAX_QADAM` | `25` | Bitta javobdagi vosita chaqiruvlari chegarasi |
| `MAX_TOKENS` | `64000` | Bitta javob uzunligi chegarasi |
| `MAX_SUHBAT` | `200` | Xotirada saqlanadigan suhbatlar soni |

## Xarajat

Claude Opus 5.5 narxi: kirish — $4, chiqish — $20 (1 mln token uchun). Skill yo'riqnomasi (~10 ming token) keshlanadi, shuning uchun takroriy so'rovlarda u ~10 baravar arzon hisoblanadi. Har javob ostida sarflangan tokenlar soni ko'rsatiladi. Arzonroq ishlatish uchun `EFFORT=medium` qo'ying. Veb-qidiruv alohida haq olinadi.

## Xavfsizlik

- Serverni internetga ochsangiz: `KIRISH_PAROLI` qo'ying va HTTPS (masalan, Caddy yoki nginx) orqasida ishlating.
- Agent faqat `bilim/` papkasidagi fayllarni o'qiy oladi; undan tashqariga chiqish bloklangan.
- Suhbatlar faqat server xotirasida turadi — server qayta ishga tushsa, o'chadi. Diskka hech narsa yozilmaydi.
- Agent — yordamchi vosita. Xarid usulini yakuniy tanlash, hujjatni imzolash, platformaga yuborish va ERI — foydalanuvchining javobgarligi.

## Cheklovlar

- Skilldagi "jonli jurnal" (Claude artifact sahifasi) bu ilovada yo'q: agent boshlang'ich jurnal yozuvlarini o'qiydi, yangi kuzatuvni esa javob oxirida "JURNAL YOZUVI" bloki qilib beradi.
- Word/Excel fayl yaratilmaydi — natija chatda nusxa-joylashga tayyor matn sifatida beriladi.

## Tuzilma

```
app/
  server.py      — FastAPI: chat sahifasi va oqimli (SSE) API
  agent.py       — Claude bilan muloqot tsikli, suhbat tarixi
  vositalar.py   — agent vositalari (tools)
  bilim.py       — bilim bazasi: fayl o'qish, qidiruv, klassifikator
  config.py      — sozlamalar
  static/index.html — chat interfeysi
scripts/bilim_yukla.py — bilim bazasini skilldan ko'chirish
tests/           — testlar (Claude API soxta javoblar bilan simulyatsiya qilinadi)
```

## Testlar

```bash
pip install -r requirements-dev.txt
python -m pytest
```
