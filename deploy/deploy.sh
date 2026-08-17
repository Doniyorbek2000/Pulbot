#!/usr/bin/env bash
#
# PulBot — serverga o'rnatish va yangilash skripti.
#
#   Birinchi o'rnatish:   sudo ./deploy/deploy.sh install
#   Yangilash:            ./deploy/deploy.sh update
#   Holatni ko'rish:      ./deploy/deploy.sh status
#   Loglar:               ./deploy/deploy.sh logs
#
# Ubuntu 22.04 / 24.04 va Debian 12 da sinalgan.
#
set -euo pipefail

APP_NAME="pulbot"
APP_DIR="${APP_DIR:-/opt/pulbot}"
APP_USER="${APP_USER:-pulbot}"
REPO_URL="${REPO_URL:-https://github.com/Doniyorbek2000/Pulbot.git}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

# BRANCH ko'rsatilmagan bo'lsa repozitoriyning default branchi olinadi —
# shunda "main" hali yaratilmagan bo'lsa ham skript ishlayveradi.
detect_branch() {
    local ref
    ref=$(git ls-remote --symref "$REPO_URL" HEAD 2>/dev/null \
          | awk '/^ref:/ {sub("refs/heads/", "", $2); print $2; exit}')
    echo "${ref:-main}"
}
BRANCH="${BRANCH:-$(detect_branch)}"

# Ranglar
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'

info()  { echo "${GREEN}==>${NC} $*"; }
warn()  { echo "${YELLOW}!!${NC} $*"; }
fail()  { echo "${RED}XATO:${NC} $*" >&2; exit 1; }

need_root() {
    [[ $EUID -eq 0 ]] || fail "Bu buyruq root huquqini talab qiladi: sudo $0 $*"
}

# ---------------------------------------------------------------------------
# O'rnatish
# ---------------------------------------------------------------------------

cmd_install() {
    need_root install

    info "Tizim paketlari o'rnatilmoqda..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl tzdata

    if ! id "$APP_USER" &>/dev/null; then
        info "Foydalanuvchi yaratilmoqda: $APP_USER"
        useradd --system --create-home --home-dir "/home/$APP_USER" --shell /bin/bash "$APP_USER"
    fi

    if [[ -d "$APP_DIR/.git" ]]; then
        info "Repozitoriy allaqachon bor, yangilanmoqda..."
        git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
        git -C "$APP_DIR" reset --hard "origin/$BRANCH"
    else
        info "Repozitoriy klonlanmoqda: $REPO_URL ($BRANCH)"
        rm -rf "$APP_DIR"
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    fi

    mkdir -p "$APP_DIR/data"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"

    info "Python muhiti tayyorlanmoqda..."
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

    if [[ ! -f "$APP_DIR/.env" ]]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
        chmod 600 "$APP_DIR/.env"
        warn "$APP_DIR/.env yaratildi — BOT_TOKEN, BOT_USERNAME va ADMIN_IDS ni to'ldiring:"
        warn "    sudo nano $APP_DIR/.env"
        warn "So'ng ishga tushiring:  sudo systemctl start $APP_NAME"
    fi

    install_service
    systemctl daemon-reload
    systemctl enable "$APP_NAME" >/dev/null

    if grep -q '^BOT_TOKEN=123456:' "$APP_DIR/.env" 2>/dev/null; then
        warn ".env hali to'ldirilmagan — bot ishga tushirilmadi."
    else
        systemctl restart "$APP_NAME"
        sleep 2
        cmd_status
    fi

    info "O'rnatish tugadi."
}

install_service() {
    info "systemd xizmati sozlanmoqda..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PulBot — Telegram Stars pullik xabar tizimi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python -m bot
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

# Xavfsizlik cheklovlari
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

StandardOutput=journal
StandardError=journal
SyslogIdentifier=$APP_NAME

[Install]
WantedBy=multi-user.target
EOF
}

# ---------------------------------------------------------------------------
# Yangilash
# ---------------------------------------------------------------------------

cmd_update() {
    [[ -d "$APP_DIR/.git" ]] || fail "$APP_DIR topilmadi. Avval: sudo $0 install"

    info "Kod yangilanmoqda ($BRANCH)..."
    local before
    before=$(git -C "$APP_DIR" rev-parse HEAD)

    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"

    local after
    after=$(git -C "$APP_DIR" rev-parse HEAD)

    if [[ "$before" == "$after" ]]; then
        info "Yangilik yo'q ($(git -C "$APP_DIR" rev-parse --short HEAD))."
    else
        info "Yangilandi: ${before:0:7} -> ${after:0:7}"
    fi

    info "Bog'liqliklar tekshirilmoqda..."
    "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

    # .env.example da yangi kalit paydo bo'lgan bo'lsa ogohlantiramiz
    local missing
    missing=$(comm -23 \
        <(grep -oE '^[A-Z_]+=' "$APP_DIR/.env.example" | sort -u) \
        <(grep -oE '^[A-Z_]+=' "$APP_DIR/.env" | sort -u) || true)
    if [[ -n "$missing" ]]; then
        warn ".env da yetishmayotgan sozlamalar (standart qiymat ishlatiladi):"
        echo "$missing" | sed 's/^/    /'
    fi

    info "Xizmat qayta ishga tushirilmoqda..."
    systemctl restart "$APP_NAME" 2>/dev/null || sudo systemctl restart "$APP_NAME"
    sleep 2
    cmd_status
}

# ---------------------------------------------------------------------------
# Holat va loglar
# ---------------------------------------------------------------------------

cmd_status() {
    if systemctl is-active --quiet "$APP_NAME"; then
        info "Bot ishlayapti ✅  ($(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo '?'))"
        systemctl status "$APP_NAME" --no-pager --lines=0 | sed -n '1,4p'
    else
        warn "Bot ishlamayapti ❌"
        echo "Oxirgi loglar:"
        journalctl -u "$APP_NAME" -n 25 --no-pager | sed 's/^/    /'
        return 1
    fi
}

cmd_logs()    { journalctl -u "$APP_NAME" -f --no-pager; }
cmd_restart() { systemctl restart "$APP_NAME" && cmd_status; }
cmd_stop()    { systemctl stop "$APP_NAME" && warn "Bot to'xtatildi."; }

cmd_backup() {
    local target="${1:-$APP_DIR/backups}"
    mkdir -p "$target"
    local stamp
    stamp=$(date +%Y%m%d-%H%M%S)
    if [[ -f "$APP_DIR/pulbot.db" ]]; then
        # SQLite'ni ishlab turgan holda xavfsiz nusxalash
        sqlite3 "$APP_DIR/pulbot.db" ".backup '$target/pulbot-$stamp.db'" 2>/dev/null \
            || cp "$APP_DIR/pulbot.db" "$target/pulbot-$stamp.db"
        info "Zaxira nusxa: $target/pulbot-$stamp.db"
        # 14 kundan eski nusxalarni tozalaymiz
        find "$target" -name 'pulbot-*.db' -mtime +14 -delete
    else
        warn "SQLite bazasi topilmadi (PostgreSQL ishlatilayotgan bo'lishi mumkin)."
    fi
}

usage() {
    cat <<EOF
PulBot deploy skripti

  sudo $0 install    — birinchi marta o'rnatish
  $0 update          — kodni yangilab, qayta ishga tushirish
  $0 status          — holatni ko'rish
  $0 logs            — jonli loglar
  $0 restart         — qayta ishga tushirish
  $0 stop            — to'xtatish
  $0 backup [papka]  — bazaning zaxira nusxasi

Muhit o'zgaruvchilari: APP_DIR, APP_USER, REPO_URL, BRANCH
EOF
}

case "${1:-}" in
    install) cmd_install ;;
    update)  cmd_update ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    restart) cmd_restart ;;
    stop)    cmd_stop ;;
    backup)  cmd_backup "${2:-}" ;;
    *)       usage; exit 1 ;;
esac
