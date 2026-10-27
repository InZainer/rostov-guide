// Клиент API. Сессия — в HttpOnly-cookie, поэтому токенов в JS нет вовсе.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Ошибка ${status}`);
    this.status = status;
  }
}

async function request(method, url, body) {
  let resp;
  try {
    resp = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Нет связи с сервером");
  }
  if (resp.status === 204) return null;
  let data = null;
  try { data = await resp.json(); } catch { /* пустое тело */ }
  if (!resp.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = "Проверьте введённые данные";
    throw new ApiError(resp.status, detail);
  }
  return data;
}

export const api = {
  me: () => request("GET", "/api/auth/me"),
  login: (username, password) => request("POST", "/api/auth/login", { username, password }),
  register: (username, password) => request("POST", "/api/auth/register", { username, password }),
  logout: () => request("POST", "/api/auth/logout", {}),
  chats: () => request("GET", "/api/chats"),
  createChat: () => request("POST", "/api/chats", {}),
  chat: (id) => request("GET", `/api/chats/${encodeURIComponent(id)}`),
  deleteChat: (id) => request("DELETE", `/api/chats/${encodeURIComponent(id)}`),
  ask: (id, text, location) => request("POST", `/api/chats/${encodeURIComponent(id)}/messages`,
    { text, location: location || null }),
  cancel: (runId) => request("POST", `/api/runs/${encodeURIComponent(runId)}/cancel`, {}),
  status: () => request("GET", "/api/status"),
  reverse: (lat, lon) => request("GET", `/api/reverse?lat=${lat}&lon=${lon}`),
};

// Поток событий прогона. EventSource сам переподключается при обрыве и шлёт
// Last-Event-ID — сервер досылает пропущенное. После «done» поток закрываем сами.
export function follow(runId, { onEvent, onEnd }) {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  let finished = false;
  source.onmessage = (msg) => {
    let event;
    try { event = JSON.parse(msg.data); } catch { return; }
    onEvent(event);
    if (event.type === "done") {
      finished = true;
      source.close();
      onEnd && onEnd(event);
    }
  };
  source.onerror = () => {
    // CLOSED — сервер ответил 204/404: прогона больше нет. Иначе браузер переподключится сам.
    if (source.readyState === EventSource.CLOSED && !finished) {
      finished = true;
      onEnd && onEnd(null);
    }
  };
  return () => { finished = true; source.close(); };
}
