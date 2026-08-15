# PulBot ⭐

**Telegram Stars asosidagi pullik xabar va hamyon tizimi.**
Sizga yozish uchun haq oladigan bot — shaxsiy xabarlar, guruhlar va kanallar uchun.
Interfeys 3 tilda: 🇺🇿 o'zbekcha, 🇷🇺 ruscha, 🇬🇧 inglizcha.

---

## Asosiy g'oya

Telegram Premium'da "xabarlar uchun haq olish" funksiyasi bor, lekin u faqat
Premium egalari uchun, faqat yulduzchada va sozlash imkoniyati juda cheklangan.
PulBot xuddi shu narsani **hammaga**, **istalgan valyutada** va **ancha
chuqurroq sozlamalar bilan** beradi:

| | Telegram Premium | PulBot |
|---|---|---|
| Kimga | Faqat Premium egalari | Hammaga |
| Narx birligi | Faqat yulduzcha | So'm, dollar yoki yulduzcha |
| Vaqt bo'yicha narx | Yo'q | ✅ Kun/soat bo'yicha jadval |
| Guruh va kanallar | Yo'q | ✅ To'liq |
| Kontent turi bo'yicha narx | Yo'q | ✅ Rasm/video/havola alohida |
| Kafolat (escrow) | Yo'q | ✅ Javob bermasa pul qaytadi |
| Pul yechish | Faqat Telegram orqali | Karta, Payme, Click, USDT |

---

## Qanday ishlaydi

```
1. Siz narx belgilaysiz       →  "Menga yozish 5 000 so'm"
2. Havolangizni ulashasiz     →  t.me/PulBot?start=u_a3f9k2x1
3. Yozuvchi to'laydi          →  balansidan yechiladi (Telegram Stars orqali to'ldirilgan)
4. Xabar sizga yetkaziladi    →  pul kafolatda turadi
5. Siz javob berasiz          →  pul darhol hisobingizga o'tadi
6. Pulni yechasiz             →  karta / Payme / Click / USDT
```

**Kafolat (escrow):** pul darhol emas, siz javob berganingizdan keyin o'tadi.
Agar belgilangan muddat ichida javob bermasangiz — sozlamaga qarab pul
sizga o'tadi yoki yozuvchiga qaytariladi. Bu ikkala tomon uchun ham halol.

---

## Imkoniyatlar

### 💬 Shaxsiy xabarlar
- **5 ta rejim:** ochiq, pullik, faqat Premium, Premium bepul + qolganlar pullik, yopiq
- **Narx istalgan valyutada** — so'm, dollar yoki yulduzcha (ichkarida bir xil saqlanadi)
- **Hisoblash usuli:** har bir xabar uchun yoki sessiya uchun (bir marta to'lab, N daqiqa cheksiz yozish)
- **Vaqt jadvali:** masalan tunda (22:00–08:00) narx 3x, ish kunlari 09:00–18:00 bepul, dam olish kunlari yopiq
- **Istisnolar:** do'stlar bepul, spamchilar bloklangan, VIP uchun alohida narx
- **Limitlar:** kuniga jami nechta xabar, bitta odamdan kuniga nechta
- **Birinchi xabar bepul** (tanishuv uchun)
- **Kafolat muddati** va javob bo'lmasa avtomatik qaytarish
- Javob berish tugma orqali yoki oddiy reply orqali

### 👥 Guruhlar va kanallar
- Har bir xabar uchun to'lov, tushum guruh egasining hamyoniga
- **Kontent turi bo'yicha narx:** matn 1 000, rasm 3 000, havola 10 000 so'm
- **Bepul kvotalar:** kuniga N ta bepul, yangi a'zoga birinchi N ta bepul
- Adminlar / Premium egalari / tanlangan odamlar uchun bepul
- Vaqt jadvali (guruhda ham)
- To'lanmagan xabar avtomatik o'chiriladi + ogohlantirish (o'zi ham o'chadi)
- Guruh buyruqlari: `/sozlash`, `/narx 5000`, `/bepul @user`, `/bloklash @user`

### 💰 Hamyon va to'lovlar
- Telegram Stars orqali to'ldirish (butun invoice oqimi: `pre_checkout` → `successful_payment`)
- Balans so'm / dollar / yulduzchada ko'rsatiladi — kurs admin panelidan boshqariladi
- To'liq tranzaksiya tarixi (audit uchun hech narsa o'chirilmaydi)
- Idempotentlik: bir to'lov ikki marta hisoblanmaydi
- To'lovni qaytarish (`refundStarPayment`) qo'llab-quvvatlanadi

### 💸 Pul yechish
- Usullar: karta (UZS), Payme, Click, USDT (TRC-20), yulduzcha
- Rekvizitlar formati tekshiriladi (16 raqamli karta, TRC-20 manzil va h.k.)
- **Xavfsizlik muddati:** yaqinda to'ldirilgan mablag' 72 soat yechilmaydi
  (ishlab topilgan pulga bu cheklov qo'llanmaydi)
- **Anti-fraud baholash:** yangi hisob, so'ralgan summadan kam ishlangan,
  yaqinda to'ldirilgan, avval rad etilgan — hammasi admin ko'radigan risk balliga qo'shiladi
- Kunlik limit, komissiya, minimal summa
- Mablag' so'rov paytida **ushlab turiladi** (`locked`) — ikki marta yechib bo'lmaydi
- Holatlar: kutilmoqda → tasdiqlandi → to'landi / rad etildi / bekor qilindi

### 🛠 Admin panel
- Statistika: foydalanuvchilar, aylanma, komissiya daromadi, kutilayotgan to'lovlar
- Foydalanuvchi qidirish, balans qo'shish/yechish, bloklash
- Pul yechish so'rovlarini tasdiqlash / rad etish / to'landi deb belgilash
- Kurslar va komissiyani ish vaqtida o'zgartirish (qayta ishga tushirmasdan)
- Texnik ish rejimi, pul yechishni to'xtatish
- Xabar tarqatish (flood limitini hisobga olgan holda)

---

## O'rnatish

```bash
git clone <repo>
cd Pulbot

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env ichida BOT_TOKEN, BOT_USERNAME va ADMIN_IDS ni to'ldiring

python -m bot
```

### ⚠️ @BotFather'da majburiy sozlash

Guruhda pullik rejim ishlashi uchun bot guruhdagi **barcha xabarlarni ko'rishi**
kerak. Standart holatda Telegram buni taqiqlaydi:

```
@BotFather → /mybots → botingiz → Bot Settings → Group Privacy → Turn off
```

Bu qilinmasa bot guruhda faqat buyruqlarni ko'radi va hech kimdan pul yecha
olmaydi. Shuningdek bot guruhda **admin** bo'lishi va **"Delete messages"**
huquqiga ega bo'lishi kerak — aks holda to'lanmagan xabarni o'chira olmaydi.

Kanallar uchun: kanal postlari ostidagi izohlar bog'langan muhokama guruhi
orqali o'tadi, shuning uchun guruh mantiqi ular uchun ham to'liq ishlaydi.

### Docker orqali

```bash
cp .env.example .env   # to'ldiring
docker compose up -d
docker compose logs -f bot
```

### Serverga joylash

VPS'ga o'rnatish bitta buyruq bilan:

```bash
git clone https://github.com/Doniyorbek2000/Pulbot.git /opt/pulbot
cd /opt/pulbot && sudo ./deploy/deploy.sh install
```

Batafsil qo'llanma — o'rnatish, avtomatik deploy, zaxira nusxa va
xavfsizlik sozlamalari: **[deploy/README.md](deploy/README.md)**

---

## Sozlamalar (`.env`)

| O'zgaruvchi | Ma'nosi | Standart |
|---|---|---|
| `BOT_TOKEN` | @BotFather tokeni | — |
| `BOT_USERNAME` | Bot username'i (deep-link uchun) | — |
| `ADMIN_IDS` | Adminlar ID'si, vergul bilan | — |
| `DATABASE_URL` | SQLite yoki PostgreSQL | `sqlite+aiosqlite:///pulbot.db` |
| `REDIS_URL` | FSM uchun (bo'sh = xotira) | — |
| `DEFAULT_LANGUAGE` | `uz` / `ru` / `en` | `uz` |
| `COMMISSION_BPS` | Platforma komissiyasi (500 = 5%) | `500` |
| `MIN_WITHDRAW_STARS` | Minimal yechish | `1000` |
| `WITHDRAW_FEE_BPS` | Yechish komissiyasi | `200` |
| `WITHDRAW_HOLD_HOURS` | To'ldirishdan keyingi xavfsizlik muddati | `72` |
| `DEFAULT_HOLD_HOURS` | Standart escrow muddati | `48` |
| `RATE_UZS_PER_STAR` | 1 ⭐ necha so'm | `170` |
| `WEBHOOK_URL` | Bo'sh bo'lsa long-polling | — |

Kurs va komissiya keyinchalik **admin panelidan** o'zgartiriladi — `.env` faqat
boshlang'ich qiymatlarni beradi.

---

## Loyiha tuzilishi

```
bot/
├── config.py            # .env dan sozlamalar
├── i18n.py              # 3 tilli tizim
├── main.py              # ishga tushirish (polling / webhook)
├── scheduler.py         # fon vazifalari (escrow, sessiyalar, tozalash)
├── states.py            # FSM holatlari
├── locales/             # uz.json, ru.json, en.json
├── db/
│   ├── models.py        # 16 ta jadval
│   └── enums.py         # rejimlar, holatlar, turlar
├── services/            # biznes-mantiq (handler'lardan mustaqil)
│   ├── wallet.py        # ⭐ barcha pul harakatlari shu yerdan o'tadi
│   ├── pricing.py       # ⭐ "kim, qachon, qancha to'laydi"
│   ├── relay.py         # xabar yetkazish va escrow
│   ├── withdrawals.py   # pul yechish + anti-fraud
│   ├── payments.py      # Telegram Stars
│   ├── chats.py         # guruhlar
│   ├── access.py        # istisnolar va limitlar
│   └── app_settings.py  # ish vaqtidagi sozlamalar (keshli)
├── handlers/            # Telegram bilan muloqot
├── keyboards/           # tugmalar va callback'lar
├── middlewares/         # DB sessiyasi, foydalanuvchi, til, anti-flood
└── utils/
    ├── money.py         # mXTR arifmetikasi
    └── timeutils.py     # vaqt jadvallari
```

### Muhim me'moriy qarorlar

**1. Pul `mXTR` da hisoblanadi (1 yulduzcha = 1000 mXTR).**
Komissiya 5% bo'lganda 1 yulduzchadan 0.05 ni ajratish kerak — butun sonlarda
buni yo'qotishsiz qilish uchun ichki birlik maydalangan. Telegram'ga invoice
yuborishda esa butun yulduzchaga **yuqoriga** yaxlitlanadi.

**2. Barcha pul harakatlari `services/wallet.py` orqali.**
Handler'lar hech qachon `balance_mxtr` ni to'g'ridan-to'g'ri o'zgartirmaydi.
Har bir harakat `transactions` jadvaliga yoziladi va `idempotency_key`
takrorlanishning oldini oladi.

**3. Narx faqat `services/pricing.py` da hisoblanadi.**
Rejim, istisno, jadval, limit, Premium, kvota — hammasi bitta funksiyada
ustuvorlik tartibida. Handler faqat `quote_dm()` yoki `quote_chat()` ni chaqiradi.

**4. `locked_mxtr` — ushlab turilgan mablag'.**
Escrow va pul yechish so'rovlari uchun. Balansda ko'rinadi, lekin sarflab
bo'lmaydi. Shu tufayli bitta pulni ikki marta yechish mumkin emas.

---

## Testlar

```bash
python -m pytest -q
```

69 ta test: pul arifmetikasi, hamyon (ortiqcha yechish, idempotentlik,
ushlangan mablag'), narxlash (rejimlar, istisnolar, vaqt mintaqasi bilan
jadval, limitlar, guruh kvotalari), escrow (javob/rad etish/muddat tugashi,
pulning saqlanishi) va pul yechishning to'liq oqimi.

---

## Nimani bilib qo'yish kerak

**Yulduzchani pulga aylantirish botning o'zida bo'lmaydi.** Foydalanuvchilar
to'lagan yulduzchalar bot egasining Telegram hisobiga tushadi va ularni
Telegram'ning o'z qoidalari bo'yicha (21 kundan keyin TON'ga yoki reklama
uchun) yechib olasiz. Shuning uchun foydalanuvchiga to'lov **platformadan
tashqarida** — karta, Payme, Click yoki USDT orqali qilinadi. Kod shu
modelga qurilgan: so'rov avtomatik yaratiladi, tekshiriladi va mablag'
ushlanadi, admin to'lovni amalga oshirib "to'landi" deb belgilaydi.

Bu — vositachilik biznesining odatiy modeli, lekin buni oldindan hisobga
olish kerak: sizda foydalanuvchilarga to'lash uchun aylanma mablag' bo'lishi
va yulduzchalarni muntazam yechib turishingiz lozim.

---

## Keyingi bosqich: avtomatik to'lov integratsiyasi

Hozir to'ldirish faqat Telegram Stars orqali. So'mda avtomatik to'lov
(Click / Payme / Uzum) qo'shish uchun kod tayyorlab qo'yilgan:

- `Payment` modelida `provider`, `external_ref`, `raw` maydonlari bor
- `PaymentProvider` da provayder nomlari sanab qo'yilgan
- `payments.credit_payment()` provayderga bog'liq emas — har qanday
  provayderdan kelgan tasdiqni qabul qiladi

Qo'shish uchun kerak bo'ladigan narsa: provayder uchun invoice yaratish
funksiyasi va webhook endpoint (`main.py` dagi aiohttp ilovasiga qo'shiladi),
u to'lovni tekshirib `credit_payment()` ni chaqiradi. Pul yechish tomonida
ham xuddi shunday — hozir admin qo'lda tasdiqlaydi, keyin bank API'si
`withdrawals.mark_paid()` ni chaqiradigan bo'ladi.

---

## Litsenziya

MIT
