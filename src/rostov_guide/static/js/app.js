// Контроллер приложения: вход, список чатов, отправка вопросов, подписка на поток агента.

import { api, ApiError, follow } from "./api.js";
import { AssistantView, systemNote, userMessage } from "./chat.js";
import { PlaceMap } from "./map.js";
import { fmtTokens, h, icon, relTime, storage, toast } from "./ui.js";

const $ = (id) => document.getElementById(id);

const EXAMPLES = [
  { icon: "🥟", title: "Забыл название", text: "Забыл, как называется кафе, — помню только, что там были пельмени" },
  { icon: "💊", title: "Ближайшее к адресу", text: "Где ближайшая аптека к Белорусской, 44, и до скольки она работает?" },
  { icon: "🌙", title: "Сколько", text: "Сколько в Ростове круглосуточных аптек?" },
  { icon: "📍", title: "Рядом со мной", text: "Где рядом со мной поесть грузинскую кухню?", needsLocation: true },
];

const state = {
  user: null,
  chats: [],
  chatId: null,
  run: null,               // { runId, view, chatId, stop }
  location: null,
  status: null,
  lastQuestion: "",
};

let map;
const scroller = {
  near: true,
  stick() {
    const box = $("messages");
    if (this.near) box.scrollTop = box.scrollHeight;
  },
};

// ======================================================================= вход

function showAuth() {
  $("boot").hidden = true;
  $("app").hidden = true;
  $("auth").hidden = false;
  let mode = "login";
  const setMode = (next) => {
    mode = next;
    document.querySelectorAll(".auth-tab").forEach((tab) => {
      const on = tab.dataset.mode === mode;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", String(on));
    });
    $("authSubmit").textContent = mode === "login" ? "Войти" : "Создать аккаунт";
    $("authHint").textContent = mode === "login"
      ? "Нет аккаунта? Регистрация занимает пять секунд."
      : "Имя: от 3 символов. Пароль: от 6 символов.";
    $("authForm").password.autocomplete = mode === "login" ? "current-password" : "new-password";
    $("authError").hidden = true;
  };
  document.querySelectorAll(".auth-tab").forEach((tab) => { tab.onclick = () => setMode(tab.dataset.mode); });
  $("authForm").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    const username = form.username.value.trim();
    const password = form.password.value;
    const error = $("authError");
    if (username.length < 3) { error.textContent = "Имя — не короче 3 символов"; error.hidden = false; return; }
    if (mode === "register" && password.length < 6) { error.textContent = "Пароль — не короче 6 символов"; error.hidden = false; return; }
    $("authSubmit").disabled = true;
    try {
      state.user = mode === "login" ? await api.login(username, password) : await api.register(username, password);
      $("auth").hidden = true;
      await showApp();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      $("authSubmit").disabled = false;
    }
  };
  setTimeout(() => $("authForm").username.focus(), 50);
}

// ======================================================================= приложение

async function showApp() {
  $("boot").hidden = true;
  $("app").hidden = false;
  $("userName").textContent = state.user.username;
  $("userAvatar").textContent = state.user.username.slice(0, 1);
  state.location = storage.get(`loc:${state.user.id}`);

  if (!map) {
    map = new PlaceMap($("map"), { onPick: onMapPick, onSelect: () => {} });
    map.init(isDark());
    bindUi();
  }
  map.setUser(state.location);
  renderLocation();
  await Promise.all([loadChats(), refreshStatus()]);
  const fromHash = location.hash.match(/^#\/c\/([\w-]+)/);
  if (fromHash) await openChat(fromHash[1]);
  else newChat();
}

function bindUi() {
  const input = $("input");
  input.addEventListener("input", () => { autosize(); updateSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (text && !state.run) send(text);
  });
  $("stop").onclick = () => state.run && api.cancel(state.run.runId).catch(() => {});
  $("newChat").onclick = () => { newChat(); closeDrawers(); input.focus(); };
  $("logout").onclick = async () => { await api.logout().catch(() => {}); location.hash = ""; location.reload(); };
  $("themeToggle").onclick = toggleTheme;
  updateThemeIcon();
  $("pickLocation").onclick = () => (map.picking ? cancelPick() : startPick());
  $("cancelPick").onclick = cancelPick;
  $("clearLocation").onclick = () => setLocation(null);
  $("openSidebar").onclick = () => {
    $("app").classList.add("sidebar-open");
    $("scrim").hidden = false;
  };
  $("closeSidebar").onclick = closeDrawers;
  $("scrim").onclick = closeDrawers;
  $("mapToggle").onclick = () => toggleMap();
  $("mapClose").onclick = () => toggleMap(false);
  document.addEventListener("place-focus", () => { if (window.innerWidth <= 1180) toggleMap(true); });
  $("messages").addEventListener("scroll", () => {
    const box = $("messages");
    scroller.near = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
  });
  window.addEventListener("hashchange", () => {
    const m = location.hash.match(/^#\/c\/([\w-]+)/);
    if (m && m[1] !== state.chatId) openChat(m[1]);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { cancelPick(); closeDrawers(); toggleMap(false); }
    if (e.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
      e.preventDefault();
      input.focus();
    }
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { map.setTheme(isDark()); updateThemeIcon(); });
}

function autosize() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  input.style.overflowY = input.scrollHeight > 180 ? "auto" : "hidden";
}

function updateSend() {
  $("send").disabled = !$("input").value.trim() || !!state.run;
}

function setRunning(on) {
  $("send").hidden = on;
  $("stop").hidden = !on;
  updateSend();
}

function closeDrawers() {
  $("app").classList.remove("sidebar-open");
  $("scrim").hidden = true;
}

function toggleMap(force) {
  const app = $("app");
  const open = force === undefined ? !app.classList.contains("map-open") : force;
  app.classList.toggle("map-open", open);
  $("mapToggle").classList.toggle("active", open);
  if (open) map.invalidate();
}

// ======================================================================= тема

function isDark() {
  const theme = document.documentElement.dataset.theme;
  if (theme) return theme === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
}

function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  storage.set("theme", next);
  map.setTheme(next === "dark");
  updateThemeIcon();
}

function updateThemeIcon() {
  $("themeToggle").replaceChildren(icon(isDark() ? "sun" : "moon"));
}

// ======================================================================= состояние системы

let statusTimer;

async function refreshStatus() {
  clearTimeout(statusTimer);
  try {
    state.status = await api.status();
    renderStatus();
  } catch { /* сервер перезапускается — попробуем позже */ }
  const busy = !state.status || !state.status.llm_ok || state.status.index.state === "building";
  statusTimer = setTimeout(() => (document.hidden ? statusTimer = setTimeout(refreshStatus, 2000) : refreshStatus()), busy ? 3000 : 15000);
}

function renderStatus() {
  const s = state.status;
  const rows = [];
  // LM Studio выгружает простаивающие модели: честно говорим, что первый ответ будет дольше,
  // и предупреждаем, если модель поднята с окном меньше, чем рассчитан агент
  let llm = { dot: "ok", text: `Модель ${s.model}`, title: "Сервер модели отвечает, модель загружена" };
  if (!s.llm_ok) {
    llm = { dot: "bad", text: `Модель ${s.model} недоступна`, title: "Проверьте, что LM Studio запущен и сервер включён" };
  } else if (s.llm_loaded === false) {
    llm = { dot: "warn", text: `Модель ${s.model} выгружена`,
      title: "LM Studio загрузит её при первом вопросе — он займёт дольше обычного. Лучше: lms load с окном 16k" };
  } else if (s.llm_context && s.llm_context < s.context_window) {
    llm = { dot: "warn", text: `Окно модели ${fmtTokens(s.llm_context)} < ${fmtTokens(s.context_window)}`,
      title: "Модель загружена с окном меньше, чем рассчитан агент: длинные разговоры могут не поместиться" };
  }
  rows.push(h("div", { class: "system-row", title: llm.title },
    h("span", { class: `dot ${llm.dot}` }), h("span", { text: llm.text })));
  const idx = s.index;
  if (idx.state === "building") {
    const pct = idx.total ? Math.round((100 * idx.done) / idx.total) : 0;
    rows.push(h("div", { class: "system-row", title: "Пока индекс строится, агент ищет обычными фильтрами" },
      h("span", { class: "dot warn" }), h("span", { text: `Поиск по смыслу: индексация ${pct}%` })));
    const bar = h("div", { class: "progress" }, h("span", { style: `width:${pct}%` }));
    rows.push(bar);
  } else if (idx.state === "error") {
    rows.push(h("div", { class: "system-row", title: idx.error || "" }, h("span", { class: "dot bad" }), h("span", { text: "Поиск по смыслу: ошибка индекса" })));
  } else {
    rows.push(h("div", { class: "system-row", title: `Эмбеддинги ${s.embed_model} · Qdrant` },
      h("span", { class: "dot ok" }), h("span", { text: `Поиск по смыслу: ${s.places} мест` })));
  }
  if (s.queue.running || s.queue.queued) {
    rows.push(h("div", { class: "system-row" }, h("span", { class: "dot warn" }),
      h("span", { text: `Модель занята: ${s.queue.running} в работе, ${s.queue.queued} ждут` })));
  }
  $("systemStatus").replaceChildren(...rows);
  $("contextMeter").title = `Сколько токенов разговор занимает в окне модели (${fmtTokens(s.context_window)}). `
    + "Когда разговор разрастается, ранние сообщения сворачиваются в сводку — память сохраняется, окно не переполняется.";
  setContext(state.context || 0);
}

function setContext(tokens) {
  state.context = tokens;
  const windowSize = (state.status && state.status.context_window) || 16384;
  const pct = Math.min(100, (100 * tokens) / windowSize);
  $("contextFill").style.width = `${pct}%`;
  $("contextValue").textContent = `${fmtTokens(tokens)} / ${fmtTokens(windowSize)}`;
  $("contextMeter").classList.toggle("high", pct > 60);
}

// ======================================================================= чаты

async function loadChats() {
  try {
    state.chats = await api.chats();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return location.reload();
    toast(err.message, "bad");
  }
  renderChatList();
}

function renderChatList() {
  const list = $("chatList");
  if (!state.chats.length) {
    list.replaceChildren(h("div", { class: "chat-list-empty", text: "Здесь появятся ваши разговоры" }));
    return;
  }
  list.replaceChildren(...state.chats.map((chat) => {
    const del = h("button", { class: "icon-btn delete", type: "button", "aria-label": "Удалить чат", title: "Удалить чат" }, icon("trash"));
    del.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!confirm(`Удалить чат «${chat.title}»?`)) return;
      try {
        await api.deleteChat(chat.id);
        state.chats = state.chats.filter((c) => c.id !== chat.id);
        if (state.chatId === chat.id) newChat();
        renderChatList();
      } catch (err) { toast(err.message, "bad"); }
    });
    const item = h("a", { class: `chat-item${chat.id === state.chatId ? " active" : ""}`, href: `#/c/${chat.id}` },
      h("div", { style: "min-width:0" },
        h("div", { class: "chat-item-title", text: chat.title }),
        h("div", { class: "chat-item-meta" }, chat.busy ? h("span", { class: "busy-dot", title: "Агент отвечает" }) : null,
          h("span", { text: relTime(chat.updated_at) }))),
      del);
    item.addEventListener("click", () => closeDrawers());
    return item;
  }));
}

function detachRun() {
  if (state.run) {
    state.run.stop();
    state.run = null;
  }
  setRunning(false);
}

function newChat() {
  detachRun();
  state.chatId = null;
  if (location.hash) history.replaceState(null, "", location.pathname);
  $("chatTitle").textContent = "Новый чат";
  setContext(0);
  renderEmpty();
  renderChatList();
}

function renderEmpty() {
  const examples = EXAMPLES.map((ex) => h("button", {
    class: "example", type: "button",
    onclick: () => {
      $("input").value = ex.text;
      autosize(); updateSend();
      if (ex.needsLocation && !state.location) {
        toast("Сначала отметьте на карте, где вы, — потом отправьте вопрос");
        startPick();
        return;
      }
      $("composer").requestSubmit();
    },
  }, h("span", { class: "ex-icon", text: ex.icon }), h("b", { text: ex.title }), h("span", { text: ex.text })));
  $("messagesInner").replaceChildren(h("div", { class: "empty" },
    h("h2", { text: "Что ищем в Ростове?" }),
    h("p", { text: "Спросите словами — даже если не помните названия. Агент ищет по 8860 местам из OpenStreetMap и показывает каждый свой шаг." }),
    h("div", { class: "examples" }, ...examples)));
}

async function openChat(id) {
  detachRun();
  let chat;
  try {
    chat = await api.chat(id);
  } catch (err) {
    toast(err.status === 404 ? "Чат не найден" : err.message, "bad");
    return newChat();
  }
  state.chatId = id;
  if (location.hash !== `#/c/${id}`) history.replaceState(null, "", `#/c/${id}`);
  $("chatTitle").textContent = chat.title;
  setContext(chat.context_tokens || 0);
  renderChatList();

  let messages = chat.messages;
  let pendingQuestion = null;
  if (chat.active_run) {
    const lastUser = messages.map((m) => m.role).lastIndexOf("user");
    if (lastUser >= 0 && messages[lastUser].text === chat.active_question) {
      // прогон уже стартовал: недописанный ответ пришёл бы дважды — из истории и из потока
      messages = messages.slice(0, lastUser + 1);
    } else {
      pendingQuestion = chat.active_question;   // вопрос ещё в очереди и в сессию не попал
    }
  }
  const box = $("messagesInner");
  box.replaceChildren();
  let lastView = null;
  for (const msg of messages) {
    if (msg.role === "user") box.append(userMessage(msg.text));
    else if (msg.role === "system") box.append(systemNote("Ранние сообщения свёрнуты в сводку, чтобы разговор поместился в контекст модели", msg.text));
    else {
      lastView = AssistantView.fromHistory(msg, viewOptions());
      box.append(lastView.el);
    }
  }
  if (!messages.length && !chat.active_run) renderEmpty();
  if (lastView) lastView.showOnMap();
  scroller.near = true;
  scroller.stick();

  if (chat.active_run) {
    if (pendingQuestion) box.append(userMessage(pendingQuestion));
    const view = new AssistantView(viewOptions());
    box.append(view.el);
    attachRun(chat.active_run, view, id);
  }
}

function viewOptions() {
  return {
    map,
    model: state.status && state.status.model,
    scroller,
    onContext: (tokens) => setContext(tokens),
    onRetry: () => { if (!state.run && state.lastQuestion) send(state.lastQuestion); },
  };
}

// ======================================================================= вопросы

async function send(text) {
  state.lastQuestion = text;
  $("input").value = "";
  autosize();
  setRunning(true);
  const box = $("messagesInner");
  if (box.querySelector(".empty")) box.replaceChildren();

  try {
    if (!state.chatId) {
      const chat = await api.createChat();
      state.chatId = chat.id;
      history.replaceState(null, "", `#/c/${chat.id}`);
      state.chats.unshift(chat);
      renderChatList();
    }
  } catch (err) {
    toast(err.message, "bad");
    setRunning(false);
    return;
  }

  box.append(userMessage(text));
  const view = new AssistantView(viewOptions());
  box.append(view.el);
  scroller.near = true;
  scroller.stick();

  const chatId = state.chatId;
  try {
    const resp = await api.ask(chatId, text, state.location);
    if (resp.title) {
      $("chatTitle").textContent = resp.title;
      const chat = state.chats.find((c) => c.id === chatId);
      if (chat) chat.title = resp.title;
    }
    const chat = state.chats.find((c) => c.id === chatId);
    if (chat) { chat.busy = true; chat.updated_at = Date.now() / 1000; }
    renderChatList();
    attachRun(resp.run_id, view, chatId);
  } catch (err) {
    view.showError(err.message);
    view.finish({ status: "error" });
    setRunning(false);
  }
}

function attachRun(runId, view, chatId) {
  setRunning(true);
  const stop = follow(runId, {
    onEvent: (ev) => view.handle(ev),
    onEnd: (ev) => {
      const wasCurrent = state.run && state.run.runId === runId;
      if (wasCurrent) state.run = null;
      setRunning(false);
      loadChats();
      if (!ev && wasCurrent && state.chatId === chatId) openChat(chatId);   // поток потерян — берём из истории
    },
  });
  state.run = { runId, view, chatId, stop };
}

// ======================================================================= точка на карте

function startPick() {
  if (!map.ready) return toast("Карта не загрузилась", "bad");
  if (window.innerWidth <= 1180) toggleMap(true);
  map.startPick();
  $("mapBanner").hidden = false;
  $("pickLocation").classList.add("active");
}

function cancelPick() {
  if (!map) return;
  map.stopPick();
  $("mapBanner").hidden = true;
  $("pickLocation").classList.remove("active");
}

async function onMapPick(lat, lon) {
  cancelPick();
  let label = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  try { label = (await api.reverse(lat, lon)).label; } catch (err) {
    if (err.status === 422) return toast("Точка вне Ростова — агент знает только город", "bad");
  }
  setLocation({ lat, lon, label });
  toast(`Точка поставлена: ${label}`);
  if (window.innerWidth <= 1180) setTimeout(() => toggleMap(false), 600);
  $("input").focus();
}

function setLocation(loc) {
  state.location = loc;
  if (loc) storage.set(`loc:${state.user.id}`, loc);
  else storage.remove(`loc:${state.user.id}`);
  map.setUser(loc);
  renderLocation();
}

function renderLocation() {
  const loc = state.location;
  $("locationChip").hidden = !loc;
  $("locationLabel").textContent = loc ? `Я здесь: ${loc.label}` : "";
  $("pickLocation").title = loc ? "Переставить точку на карте" : "Я здесь: поставить точку на карте";
}

// ======================================================================= старт

(async function boot() {
  const theme = storage.get("theme");
  if (theme) document.documentElement.dataset.theme = theme;
  try {
    state.user = await api.me();
  } catch {
    return showAuth();
  }
  await showApp();
})();
