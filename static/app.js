const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";

const screenList = document.getElementById("screen-list");
const screenChat = document.getElementById("screen-chat");
const chatListEl = document.getElementById("chat-list");
const emptyState = document.getElementById("empty-state");
const messagesEl = document.getElementById("messages");
const chatTitleEl = document.getElementById("chat-title");
const backBtn = document.getElementById("back-btn");
const themeBtnList = document.getElementById("theme-btn-list");
const themeBtnChat = document.getElementById("theme-btn-chat");

// ---------- тема ----------
function applyTheme(theme) {
  document.body.classList.toggle("light", theme === "light");
  try { localStorage.setItem("theme", theme); } catch {}
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch {}
  const fromTg = tg?.colorScheme === "light" ? "light" : "dark";
  applyTheme(saved || fromTg);
}

function toggleTheme() {
  applyTheme(document.body.classList.contains("light") ? "dark" : "light");
}

themeBtnList.onclick = toggleTheme;
themeBtnChat.onclick = toggleTheme;

// ---------- запросы к API ----------
async function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url, { headers: { "X-Init-Data": initData } });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function initials(name) {
  return (name || "?").trim().split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.innerText = s ?? "";
  return d.innerHTML;
}

// ---------- аватарки ----------
async function loadAvatar(el, chatId) {
  try {
    const res = await fetch(`/api/avatar?chat_id=${chatId}`, {
      headers: { "X-Init-Data": initData },
    });
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    el.innerHTML = `<img src="${url}" alt="">`;
  } catch {
    // нет аватарки — остаются инициалы
  }
}

// ---------- список чатов ----------
async function loadChats() {
  const chats = await api("/api/chats");
  chatListEl.innerHTML = "";
  if (!chats.length) {
    emptyState.style.display = "flex";
    return;
  }
  emptyState.style.display = "none";
  for (const c of chats) {
    const el = document.createElement("div");
    el.className = "chat-item";
    el.innerHTML = `
      <div class="avatar">${escapeHtml(initials(c.name))}</div>
      <div class="chat-item-body">
        <div class="chat-item-top">
          <div class="chat-item-name">${escapeHtml(c.name)}</div>
          <div class="chat-item-time">${fmtTime(c.last_date)}</div>
        </div>
        <div class="chat-item-preview">${escapeHtml(c.last_text || "")}</div>
      </div>
    `;
    el.onclick = () => openChat(c.chat_id, c.name);
    chatListEl.appendChild(el);
    loadAvatar(el.querySelector(".avatar"), c.chat_id);
  }
}

// ---------- открытие чата ----------
async function openChat(chatId, name) {
  chatTitleEl.textContent = name;
  screenList.classList.remove("active");
  screenChat.classList.add("active");

  const msgs = await api("/api/messages", { chat_id: chatId });
  messagesEl.innerHTML = "";
  for (const m of msgs) {
    messagesEl.appendChild(renderBubble(m, chatId));
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

const MEDIA_LABELS = {
  photo: "🖼 Фото", voice: "🎤 Голосовое сообщение", video_note: "⭕ Видеосообщение",
  video: "📹 Видео", animation: "🎞 GIF", audio: "🎵 Аудио",
  document: "📄 Файл", sticker: "🩹 Стикер",
};

function renderBubble(m, chatId) {
  const row = document.createElement("div");
  row.className = `bubble-row ${m.is_owner ? "out" : "in"}`;

  const bubble = document.createElement("div");
  bubble.className = `bubble ${m.is_owner ? "out" : "in"}${m.deleted ? " deleted" : ""}`;

  // моноширинный текст для удалённых и изменённых сообщений
  const monoClass = (m.deleted || m.edited) ? " mono" : "";

  let html = "";

  if (m.deleted) {
    html += `<div class="bubble-tag deleted-tag">🗑 Удалено ${fmtTime(m.deleted_at)}</div>`;
  } else if (m.edited) {
    html += `<div class="bubble-tag edited-tag" data-toggle="edit">✏️ изменено</div>`;
  }

  if (m.text) {
    html += `<div class="bubble-text${monoClass}">${escapeHtml(m.text)}</div>`;
  } else if (m.mtype) {
    html += `<div class="bubble-text">${MEDIA_LABELS[m.mtype] || "📎 Вложение"}</div>`;
  }

  if (m.has_media && m.mtype) {
    html += `<div class="media-box" data-media="${m.mtype}" data-msg="${m.msg_id}">
      <div class="media-placeholder">▶ Показать</div>
    </div>`;
  }

  if (m.edited && m.edit_history?.length) {
    html += `<div class="edit-history">`;
    for (const e of m.edit_history) {
      html += `<div class="ver">
        <span class="old">${escapeHtml(e.old_text)}</span> → ${escapeHtml(e.new_text)}
        <div style="opacity:.6">${fmtTime(e.edited_at)}</div>
      </div>`;
    }
    html += `</div>`;
  }

  html += `<div class="bubble-time">${fmtTime(m.date)}</div>`;
  bubble.innerHTML = html;
  row.appendChild(bubble);

  const editTag = bubble.querySelector('[data-toggle="edit"]');
  if (editTag) {
    editTag.onclick = () => {
      bubble.querySelector(".edit-history")?.classList.toggle("open");
    };
  }

  const mediaBox = bubble.querySelector(".media-box");
  if (mediaBox) {
    mediaBox.querySelector(".media-placeholder").onclick = async () => {
      const mtype = mediaBox.dataset.media;
      const msgId = mediaBox.dataset.msg;
      mediaBox.innerHTML = `<div class="media-placeholder">Загрузка...</div>`;
      try {
        const res = await fetch(`/api/file?chat_id=${chatId}&msg_id=${msgId}`, {
          headers: { "X-Init-Data": initData },
        });
        if (!res.ok) throw new Error();
        const url = URL.createObjectURL(await res.blob());
        if (mtype === "photo" || mtype === "sticker") {
          mediaBox.innerHTML = `<img src="${url}">`;
        } else if (mtype === "voice" || mtype === "audio") {
          mediaBox.innerHTML = `<audio controls src="${url}"></audio>`;
        } else {
          mediaBox.innerHTML = `<video controls src="${url}"></video>`;
        }
      } catch {
        mediaBox.innerHTML = `<div class="media-placeholder" style="color:var(--danger)">
          Файл недоступен (срок хранения истёк)
        </div>`;
      }
    };
  }

  return row;
}

backBtn.onclick = () => {
  screenChat.classList.remove("active");
  screenList.classList.add("active");
};

initTheme();
loadChats();