# Davlat xaridlari agenti — veb-ilova

O'zbekiston davlat buyurtmachilari (byudjet va korporativ) uchun Claude asosidagi AI agent. Brauzerda ochiladigan chat orqali ishlaydi: xarid usulini tanlaydi, VMQ-480 bo'yicha narx tahlilini qiladi, klassifikator kodini topadi, raqobatni cheklamaydigan texnik topshiriq va hujjatlar tayyorlaydi, qonun matnlari asosida savollarga javob beradi.

Agentning bilimi va ish qoidalari sizning **"davlat-xarid-agent"** skillingizdan olinadi. Skill mazmuni bu repoga kirmaydi — u serverdagi `bilim/` papkasida turadi (`.gitignore` da).

## Imkoniyatlar

- **Chat sahifasi** — javoblar jonli (oqimli) chiqadi; jadval va ro'yxatlar formatlangan; kod bloklarini va butun javobni bir bosishda nusxalash mumkin; telefonda ham ishlaydi.
- **Rasm yuborish** — talabnoma surati, platforma ekrani yoki marketpleys skrinshotini biriktirasiz yoki Ctrl+V bilan qo'yasiz (katta suratlar avtomatik kichraytiriladi).
- **Agent vositalari:**
  - `kod_qidir` — klassifikator bo'yicha oflayn qidiruv (24 157 pozitsiya; lotin, kirill, rus tilida; "sut" → "ichimlik suti" kabi qo'shimchali shakllarni ham topadi);
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

Skillni yangilasangiz — shu buyruqni qayta ishga tushiring va serverni qayta yoqing. **Siz to'ldirgan profil saqlab qolinadi**; skilldagi yangi shablon uning yonida `tashkilot_profili.md.yangi-shablon` nomi bilan turadi.

Tashkilot profilini oldindan to'ldirmoqchi bo'lsangiz, `bilim/profil/tashkilot_profili.md` faylini tahrirlang — agent uni har suhbatda o'qiydi. Yoki profilni chatda yuboring (suhbatlar orasida saqlanmaydi).

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

## Boshqa kompyuterlardan yoki internetdan kirish

Parol qo'yilmagan server **faqat o'sha kompyuterning o'zidan** (`localhost`) ochiladi — aks holda tarmoqdagi istalgan kishi sizning API hisobingizdan foydalana olardi.

Hamkasblar ham ishlatishi uchun:

1. `.env` da `KIRISH_PAROLI` qo'ying (uzun, taxmin qilib bo'lmaydigan).
2. Serverni tashqi manzilda ishga tushiring: `uvicorn app.server:app --host 0.0.0.0 --port 8000`.
3. Internetga ochsangiz — HTTPS beradigan proksi (Caddy, nginx) orqasiga qo'ying va uvicorn'ga `--proxy-headers --forwarded-allow-ips=<proksi manzili>` qo'shing (parolni taxmin qilishdan himoya har bir mijoz manzili bo'yicha ishlashi uchun).

Parol to'g'ri kiritilgach brauzerga vaqtinchalik token beriladi (7 kun; server qayta ishga tushsa, qayta kiriladi). Ketma-ket 5 ta noto'g'ri paroldan so'ng o'sha manzil 1 daqiqaga bloklanadi.

## Sozlamalar (`.env`)

| O'zgaruvchi | Sukut | Ma'nosi |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API kaliti (majburiy) |
| `KIRISH_PAROLI` | bo'sh | Sahifaga kirish paroli. Bo'sh bo'lsa — faqat `localhost` |
| `RUXSAT_HOSTLAR` | bo'sh | Sahifa ochiladigan manzillar, vergul bilan (`xarid.tashkilot.uz,*.ichki.uz`). Bo'sh: parol bilan — istalgan, parolsiz — faqat `localhost` |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model |
| `EFFORT` | `high` | Fikrlash chuqurligi: `low`, `medium`, `high`, `xhigh`, `max` |
| `VEB_QIDIRUV` | `1` | Internetda qidiruv (`0` — o'chirilgan) |
| `ZAXIRA_MODEL` | `default` | Xavfsizlik klassifikatori so'rovni rad etsa, server tomonda zaxira modelga o'tish (`yoq` — o'chirilgan) |
| `BILIM_PAPKASI` | `bilim` | Bilim bazasi papkasi |
| `MAX_QADAM` | `25` | Bitta javobdagi vosita chaqiruvlari chegarasi |
| `MAX_TOKENS` | `64000` | Bitta javob uzunligi chegarasi |
| `MAX_SUHBAT` | `200` | Xotirada saqlanadigan suhbatlar soni |
| `SUHBAT_MUDDATI_SOAT` | `12` | Shuncha soat ishlatilmagan suhbat xotiradan o'chiriladi |

## Xarajat

Claude Opus 5.5 narxi: kirish — $4, chiqish — $20 (1 mln token uchun). Skill yo'riqnomasi (~10 ming token) keshlanadi, shuning uchun takroriy so'rovlarda u ~10 baravar arzon hisoblanadi. Har javob ostida sarflangan tokenlar soni ko'rsatiladi. Suhbat uzaygan sari har so'rov qimmatlashadi (butun tarix qayta yuboriladi) — yangi mavzu uchun "Yangi suhbat" bosing. Arzonroq ishlatish uchun `EFFORT=medium` qo'ying. Veb-qidiruv alohida haq olinadi.

## Xavfsizlik

- Agent faqat `bilim/` papkasidagi fayllarni o'qiy oladi; undan tashqariga chiqish (shu jumladan ramziy havolalar orqali) bloklangan. Agent vositalari hech narsani o'zgartirmaydi.
- Agent javobi sahifada qat'iy tozalanadi: rasm, forma, uslub va skriptlar ko'rsatilmaydi, sahifa tashqi manzillarga murojaat qilmaydi (Content-Security-Policy). Veb-qidiruvdan kelgan zararli matn suhbat ma'lumotini tashqariga chiqara olmaydi.
- So'rov hajmi cheklangan; parolsiz mijozning so'rovi umuman qayta ishlanmaydi.
- Suhbatlar faqat server xotirasida turadi — server qayta ishga tushsa yoki muddati o'tsa, o'chadi (sahifa buni aytadi). Diskka hech narsa yozilmaydi.
- Sahifa uchun kerakli kutubxonalar (`marked`, `DOMPurify`) repoda — ishlash uchun CDN kerak emas.
- Agent — yordamchi vosita. Xarid usulini yakuniy tanlash, hujjatni imzolash, platformaga yuborish va ERI — foydalanuvchining javobgarligi.

## Cheklovlar

- Bitta xabarda 5 tagacha, bitta suhbatda 20 tagacha rasm (jami ~15 MB). Ko'proq kerak bo'lsa — yangi suhbat.
- Skilldagi "jonli jurnal" (Claude artifact sahifasi) bu ilovada yo'q: agent boshlang'ich jurnal yozuvlarini o'qiydi, yangi kuzatuvni esa javob oxirida "JURNAL YOZUVI" bloki qilib beradi.
- Word/Excel fayl yaratilmaydi — natija chatda nusxa-joylashga tayyor matn sifatida beriladi.
- Agent platformalarga (xt-xarid, e-birja, DXMAP) o'zi kirmaydi — qadamlarni siz bajarasiz, ekran nusxasini yuborsangiz, o'qib beradi.

## Tuzilma

```
app/
  server.py      — FastAPI: chat sahifasi, oqimli (SSE) API, kirish va himoya qatlami
  agent.py       — Claude bilan muloqot tsikli, suhbat tarixi
  vositalar.py   — agent vositalari (tools)
  bilim.py       — bilim bazasi: fayl o'qish, qidiruv, klassifikator
  config.py      — sozlamalar
  static/        — chat interfeysi (index.html, app.js, style.css, vendor/)
scripts/bilim_yukla.py — bilim bazasini skilldan ko'chirish
tests/           — testlar (Claude API soxta javoblar bilan simulyatsiya qilinadi)
```

## Testlar

```bash
pip install -r requirements-dev.txt
python -m pytest
```
