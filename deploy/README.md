# Serverga o'rnatish

## ⚠️ Avval xavfsizlik haqida

**Server parolini yoki SSH kalitini hech kimga — jumladan chatda ham — yubormang.**
Quyidagi usulda kalit faqat sizning serveringiz va GitHub Secrets orasida
qoladi, uni hech kim ko'ra olmaydi (GitHub ham qayta ko'rsata olmaydi).

---

## 1-usul: Oddiy o'rnatish (tavsiya etiladi)

Eng arzon VPS ham yetadi: **1 GB RAM, 1 CPU, Ubuntu 22.04/24.04**.

### Serverga kiring va bitta buyruq bering

```bash
ssh root@SERVER_IP

git clone https://github.com/Doniyorbek2000/Pulbot.git /opt/pulbot
cd /opt/pulbot
sudo ./deploy/deploy.sh install
```

Skript o'zi hamma narsani qiladi: Python o'rnatadi, alohida foydalanuvchi
yaratadi, virtual muhit tayyorlaydi, systemd xizmatini sozlaydi va
avtomatik qayta ishga tushishni yoqadi.

### Sozlamalarni to'ldiring

```bash
sudo nano /opt/pulbot/.env
```

Kamida shu uchtasi:

```ini
BOT_TOKEN=7123456789:AAF...        # @BotFather bergan token
BOT_USERNAME=SizningBotingiz       # @ belgisisiz
ADMIN_IDS=123456789                # @userinfobot dan o'z ID'ingizni oling
```

### Ishga tushiring

```bash
sudo systemctl start pulbot
./deploy/deploy.sh status
```

### Kundalik buyruqlar

```bash
./deploy/deploy.sh status     # ishlayaptimi
./deploy/deploy.sh logs       # jonli loglar (chiqish: Ctrl+C)
./deploy/deploy.sh update     # kodni yangilash
./deploy/deploy.sh restart    # qayta ishga tushirish
./deploy/deploy.sh backup     # baza zaxirasi
```

---

## 2-usul: Docker orqali

PostgreSQL va Redis bilan birga ishga tushadi — ko'p foydalanuvchi
kutilayotgan bo'lsa shuni tanlang.

```bash
git clone https://github.com/Doniyorbek2000/Pulbot.git /opt/pulbot
cd /opt/pulbot
cp .env.example .env && nano .env
docker compose up -d
docker compose logs -f bot
```

---

## Avtomatik deploy (har push'da o'zi yangilanadi)

Bir marta sozlaysiz — keyin `main` branchga har push'da server o'zi
yangilanadi. Testlar yiqilsa deploy bo'lmaydi.

### 1-qadam. Serverda deploy uchun alohida kalit yarating

**Kalitni o'z kompyuteringizda emas, serverda yarating** — shunda maxfiy
qismi hech qayerga ko'chmaydi:

```bash
ssh root@SERVER_IP

# Parolsiz kalit juftligi (faqat deploy uchun)
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/github_deploy -N ""

# Ochiq qismini ruxsat etilganlar ro'yxatiga qo'shamiz
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Maxfiy qismini ekranga chiqaramiz — uni GitHub'ga ko'chirasiz
cat ~/.ssh/github_deploy
```

Oxirgi buyruq chiqargan matnni (`-----BEGIN OPENSSH PRIVATE KEY-----` dan
`-----END OPENSSH PRIVATE KEY-----` gacha, **hammasi**) nusxalang.

### 2-qadam. GitHub'ga saqlang

Repozitoriyda: **Settings → Secrets and variables → Actions → New repository secret**

| Nomi | Qiymati |
|---|---|
| `SERVER_HOST` | serveringiz IP manzili |
| `SERVER_USER` | `root` (yoki sudo huquqli foydalanuvchi) |
| `SERVER_SSH_KEY` | yuqorida nusxalagan maxfiy kalit |
| `SERVER_PORT` | `22` (agar boshqa port bo'lsa — o'shani) |

Saqlangandan keyin GitHub ham bu qiymatlarni qayta ko'rsatmaydi.

### 3-qadam. Ekran ortidagi terminalda tekshiring

Actions → **Serverga deploy** → **Run workflow**. Yashil belgi chiqsa —
tayyor. Bundan keyin `main` ga har push avtomatik deploy bo'ladi.

---

## Xavfsizlik bo'yicha tavsiyalar

Bot pul bilan ishlaydi, shuning uchun serverni himoyalash muhim:

```bash
# 1. Parol bilan kirishni o'chiring (faqat SSH kalit qolsin)
sudo nano /etc/ssh/sshd_config
#   PasswordAuthentication no
#   PermitRootLogin prohibit-password
sudo systemctl restart ssh

# 2. Faqat kerakli portlarni oching
sudo ufw allow OpenSSH
sudo ufw enable

# 3. Avtomatik xavfsizlik yangilanishlari
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades

# 4. Kunlik zaxira nusxa (soat 04:00 da)
sudo crontab -e
#   0 4 * * * /opt/pulbot/deploy/deploy.sh backup
```

`.env` faylida bot tokeni bor — u `chmod 600` bilan himoyalangan va
`.gitignore` da, ya'ni hech qachon GitHub'ga tushmaydi. Agar token
tasodifan oshkor bo'lsa: @BotFather → `/revoke` → yangi token oling.

---

## Muammolarni hal qilish

**Bot ishga tushmayapti**
```bash
./deploy/deploy.sh status        # sabab shu yerda ko'rinadi
journalctl -u pulbot -n 50       # batafsil loglar
```

**"BOT_TOKEN ko'rsatilmagan"** — `.env` to'ldirilmagan:
`sudo nano /opt/pulbot/.env`

**Guruhda pul yechilmayapti** — @BotFather da Group Privacy o'chirilmagan:
`/mybots → bot → Bot Settings → Group Privacy → Turn off`.
So'ng botni guruhdan chiqarib, qayta admin qilib qo'shing.

**Xotira yetmayapti** — 1 GB RAM'li serverda swap qo'shing:
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Deploy workflow yiqildi** — Actions bo'limidagi log'da sabab yozilgan.
Ko'p uchraydigani: `SERVER_SSH_KEY` to'liq ko'chirilmagan (BEGIN/END
qatorlari ham kerak) yoki `SERVER_USER` da sudo huquqi yo'q.
