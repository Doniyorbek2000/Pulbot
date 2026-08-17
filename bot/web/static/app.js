// Telegram Mini App Dashboard Logic

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.expand();
  tg.ready();
}

const initData = tg?.initData || "";
const headers = {
  "Content-Type": "application/json",
  "X-Telegram-Init-Data": initData,
};

let currentDashboardData = null;

// Tab Switcher
function switchTab(tabId) {
  if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
  document.querySelectorAll(".tab-content").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));

  const targetTab = document.getElementById(tabId);
  const targetNav = document.getElementById(`nav-${tabId}`);
  if (targetTab) targetTab.classList.add("active");
  if (targetNav) targetNav.classList.add("active");

  if (tabId === "chats") loadChats();
  if (tabId === "whitelist") loadWhitelist();
}

// Show Alert
function notify(msg) {
  if (tg?.showAlert) {
    tg.showAlert(msg);
  } else {
    alert(msg);
  }
}

// Load Dashboard Data
async function loadDashboard() {
  try {
    const res = await fetch("/api/dashboard", { headers });
    if (!res.ok) throw new Error("Dashboard ma'lumotlarini yuklab bo'lmadi");
    const data = await res.json();
    currentDashboardData = data;

    // Render User & Balances
    document.getElementById("user-name").textContent = data.user.name || "Foydalanuvchi";
    document.getElementById("biz-badge").textContent = data.user.business_enabled ? "💼 Business Faol" : "🔒 Oddiy rejim";
    document.getElementById("balance-val").textContent = `${data.balance.available_sum.toLocaleString()} UZS`;
    document.getElementById("earned-val").textContent = `${data.balance.earned_sum.toLocaleString()} UZS`;
    document.getElementById("locked-val").textContent = `${data.balance.locked_sum.toLocaleString()} UZS`;

    // Render Stats
    document.getElementById("stat-chats").textContent = data.stats.monetized_chats;
    document.getElementById("stat-subs").textContent = data.stats.active_subscribers;

    // Render DM Settings Tab
    document.getElementById("dm-toggle").checked = data.dm_settings.enabled;
    document.getElementById("dm-price-input").value = data.dm_settings.price_sum;
    document.getElementById("dm-price-slider").value = data.dm_settings.price_sum;
    document.getElementById("dm-duration-input").value = data.dm_settings.session_hours;
    document.getElementById("dm-welcome-input").value = data.dm_settings.welcome_text || "";
    if (data.dm_settings.pricing_unit) {
      document.getElementById("dm-pricing-unit").value = data.dm_settings.pricing_unit;
      onPricingUnitChange(data.dm_settings.pricing_unit);
    }
  } catch (err) {
    console.error(err);
  }
}

function onPricingUnitChange(unit) {
  const durationWrap = document.getElementById("dm-duration-wrap");
  const durationInput = document.getElementById("dm-duration-input");
  if (unit === "per_message") {
    if (durationWrap) durationWrap.style.display = "none";
  } else if (unit === "monthly") {
    if (durationWrap) durationWrap.style.display = "none";
    if (durationInput) durationInput.value = 720;
  } else {
    if (durationWrap) durationWrap.style.display = "block";
  }
}

// DM Price Slider sync
function syncPrice(val, fromSlider = true) {
  if (fromSlider) {
    document.getElementById("dm-price-input").value = val;
  } else {
    document.getElementById("dm-price-slider").value = val;
  }
}

// Save DM Settings
async function saveDMSettings() {
  const enabled = document.getElementById("dm-toggle").checked;
  const pricing_unit = document.getElementById("dm-pricing-unit").value || "session";
  const price_sum = parseInt(document.getElementById("dm-price-input").value, 10) || 10000;
  let session_hours = parseInt(document.getElementById("dm-duration-input").value, 10) || 24;
  if (pricing_unit === "monthly") session_hours = 720;
  const welcome_text = document.getElementById("dm-welcome-input").value;

  try {
    const res = await fetch("/api/settings/dm", {
      method: "POST",
      headers,
      body: JSON.stringify({ enabled, pricing_unit, price_sum, session_hours, welcome_text }),
    });
    const result = await res.json();
    if (res.ok && result.ok) {
      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      notify("✅ Shaxsiy chat sozlamalari saqlandi!");
      loadDashboard();
    } else {
      throw new Error(result.message || "Xatolik yuz berdi");
    }
  } catch (err) {
    notify(`Xatolik: ${err.message}`);
  }
}

// Load Monetized Chats
async function loadChats() {
  const container = document.getElementById("chats-list");
  container.innerHTML = "<p style='color:var(--hint-color);'>Yuklanmoqda...</p>";

  try {
    const res = await fetch("/api/chats", { headers });
    const chats = await res.json();
    if (!chats.length) {
      container.innerHTML = "<p style='color:var(--hint-color);'>Hozircha guruh yoki kanal ulanmagan. Botni guruh/kanalingizga admin qilib qo'shing.</p>";
      return;
    }
    container.innerHTML = chats
      .map(
        (c) => `
        <div class="list-item">
          <div>
            <div style="font-weight:700;">${c.title} (${c.chat_type})</div>
            <div style="font-size:12px; color:var(--hint-color);">Narx: ${c.price_sum.toLocaleString()} UZS | Tushum: ${c.earned_sum.toLocaleString()} UZS</div>
          </div>
          <div class="badge">${c.enabled ? "Faol" : "O'chiq"}</div>
        </div>
      `
      )
      .join("");
  } catch (err) {
    container.innerHTML = "<p style='color:#ef4444;'>Guruhlarni yuklashda xatolik</p>";
  }
}

// Load Whitelist
async function loadWhitelist() {
  const container = document.getElementById("whitelist-container");
  container.innerHTML = "<p style='color:var(--hint-color);'>Yuklanmoqda...</p>";

  try {
    const res = await fetch("/api/whitelist", { headers });
    const list = await res.json();
    if (!list.length) {
      container.innerHTML = "<p style='color:var(--hint-color);'>Oq ro'yxat bo'sh. Istisno qilinadigan do'stlaringizni qo'shing.</p>";
      return;
    }
    container.innerHTML = list
      .map(
        (item) => `
        <div class="list-item">
          <div>
            <div style="font-weight:700;">User ID: ${item.target_id}</div>
            <div style="font-size:12px; color:var(--hint-color);">${item.note || "Sabab ko'rsatilmagan"}</div>
          </div>
          <button class="delete-btn" onclick="deleteWhitelist(${item.id})">O'chirish</button>
        </div>
      `
      )
      .join("");
  } catch (err) {
    container.innerHTML = "<p style='color:#ef4444;'>Oq ro'yxatni yuklashda xato</p>";
  }
}

// Add Whitelist User
async function addWhitelistUser() {
  const targetId = prompt("Foydalanuvchi Telegram ID sini kiriting:");
  if (!targetId || isNaN(targetId)) return;
  const reason = prompt("Izoh (ixtiyoriy):") || "";

  try {
    const res = await fetch("/api/whitelist", {
      method: "POST",
      headers,
      body: JSON.stringify({ target_id: parseInt(targetId, 10), reason }),
    });
    if (res.ok) {
      notify("✅ Foydalanuvchi oq ro'yxatga qo'shildi!");
      loadWhitelist();
    }
  } catch (err) {
    notify("Xatolik yuz berdi");
  }
}

// Delete Whitelist User
async function deleteWhitelist(ruleId) {
  if (!confirm("Haqiqatan ham o'chirmoqchimisiz?")) return;
  try {
    await fetch(`/api/whitelist/${ruleId}`, { method: "DELETE", headers });
    loadWhitelist();
  } catch (err) {
    notify("O'chirishda xatolik");
  }
}

// Submit Payout Request
async function submitWithdraw() {
  const amount_sum = parseInt(document.getElementById("withdraw-amount").value, 10);
  const method = document.getElementById("withdraw-method").value;
  const destination = document.getElementById("withdraw-dest").value.trim();

  if (!amount_sum || amount_sum < 10000) {
    notify("Minimal yechish summasi: 10,000 UZS");
    return;
  }
  if (!destination) {
    notify("Karta yoki hamyon raqamini kiriting");
    return;
  }

  try {
    const res = await fetch("/api/withdraw", {
      method: "POST",
      headers,
      body: JSON.stringify({ amount_sum, method, destination }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      notify("🎉 Pul yechish so'rovi adminga yuborildi!");
      document.getElementById("withdraw-dest").value = "";
      loadDashboard();
      switchTab("dashboard");
    } else {
      throw new Error(data.detail || "Xatolik yuz berdi");
    }
  } catch (err) {
    notify(`Xatolik: ${err.message}`);
  }
}

// Initial Load
document.addEventListener("DOMContentLoaded", () => {
  loadDashboard();
});
