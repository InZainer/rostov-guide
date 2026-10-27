// Отрисовка сообщений: вопрос, ход мысли агента (живой), ответ, карточки мест.
// Одна и та же AssistantView принимает события из SSE и из сохранённой истории.

import { renderMarkdown } from "./markdown.js";
import { fmtDistance, fmtSeconds, fmtTokens, h, icon, plural, spinner, toast } from "./ui.js";

const TOOL_ICONS = { search_places: "search", find_places: "filter", nearest_places: "near", place_details: "card" };
const FOUND_BY = { "смысл": "по смыслу", "слова": "по словам", "смысл+слова": "смысл + слова" };
const ARG_LABELS = { query: "запрос", kind: "вид", street: "улица", name: "название", address: "адрес",
  place_id: "место", open_now: "открыто сейчас", only_24_7: "круглосуточно", limit: "сколько" };

// «Центр-инвест», "Центр-инвест" и центр-инвест — одно и то же название
const norm = (text) => (text || "").toLowerCase().replace(/[«»"'„“”`]/g, "").replace(/ё/g, "е").replace(/\s+/g, " ").trim();

export function userMessage(text) {
  return h("div", { class: "msg-user" }, h("div", { class: "bubble", text }));
}

export function systemNote(text, title) {
  return h("div", { class: "msg-system", title: title || "" , text });
}

export class AssistantView {
  constructor({ map, model, onContext, onRetry, scroller }) {
    this.map = map;
    this.model = model;
    this.onContext = onContext;
    this.onRetry = onRetry;
    this.scroller = scroller;
    this.places = new Map();           // id → карточка, в порядке появления
    this.lastResult = null;            // последний результат инструмента с местами
    this.answerText = "";
    this.toolSteps = new Map();        // id вызова → шаг
    this.toolCount = 0;
    this.stepCount = 0;
    this.started = performance.now();
    this.usage = { llm_calls: 0, input_tokens: 0, output_tokens: 0, context_tokens: 0 };
    this.thoughtStep = null;
    this.renderQueued = false;

    this.statusText = h("span", { class: "trace-status" }, h("b", { text: "Отправляю вопрос…" }));
    this.statusIcon = spinner();
    this.timer = h("span", { class: "trace-timer", "aria-hidden": "true" });
    this.lastThought = null;           // последний завершённый шаг рассуждения
    this.steps = h("div", { class: "trace-steps" });
    this.trace = h("details", { class: "trace", open: true },
      h("summary", {}, this.statusIcon, this.statusText, this.timer, icon("chevron")),
      this.steps);
    this.answer = h("div", { class: "answer streaming", "aria-live": "polite" });
    this.cards = h("div", { class: "cards", hidden: true });
    this.meta = h("div", { class: "meta", hidden: true });
    this.notes = h("div", {});
    this.el = h("div", { class: "msg-assistant" }, this.notes, this.trace, this.answer, this.cards, this.meta);
  }

  // ---------------------------------------------------------------- события

  startTimer() {
    if (this.ticker) return;
    const tick = () => { this.timer.textContent = fmtSeconds(performance.now() - this.started).replace(/,\d/, ""); };
    tick();
    this.ticker = setInterval(tick, 1000);
  }

  stopTimer() {
    clearInterval(this.ticker);
    this.ticker = null;
    this.timer.textContent = "";
  }

  handle(ev) {
    switch (ev.type) {
      case "queued": return this.setQueued(ev.position);
      case "started": this.started = performance.now(); this.startTimer(); return this.setStatus("Думаю…");
      case "thought_delta": return this.appendThought(ev.text);
      case "thought": return this.finishThought(ev.text);
      case "tool_call": return this.addToolCall(ev);
      case "tool_result": return this.addToolResult(ev);
      case "answer_delta": this.answerText += ev.text; return this.scheduleAnswer();
      case "answer": this.answerText = ev.text; return this.scheduleAnswer();
      case "usage": return this.setUsage(ev);
      case "compaction": return this.notes.append(systemNote("Ранние сообщения свёрнуты в сводку, чтобы разговор поместился в контекст модели", ev.text));
      case "error": return this.showError(ev.message);
      case "done": return this.finish(ev);
      default: return undefined;
    }
  }

  setQueued(position) {
    this.notes.querySelector(".queue-note")?.remove();
    this.setStatus("Жду очереди к модели…");
    this.notes.append(h("div", { class: "queue-note", role: "status" }, spinner(),
      h("span", { text: position <= 1
        ? "Модель занята другим вопросом — вы следующий в очереди."
        : `В очереди к модели: перед вами ${position - 1} ${plural(position - 1, "вопрос", "вопроса", "вопросов")}.` })));
  }

  setStatus(text) {
    this.notes.querySelector(".queue-note")?.remove();
    this.statusText.replaceChildren(h("b", { text }));
  }

  appendThought(text) {
    if (!this.thoughtStep) {
      this.stepCount += 1;
      const body = h("div", { class: "thought-text" });
      this.thoughtStep = { body, el: this.step("thought", "thought", "Рассуждаю", body) };
      this.setStatus("Рассуждаю…");
    }
    this.thoughtStep.body.textContent += text;
    this.stick();
  }

  finishThought(text) {
    if (!this.thoughtStep && this.lastThought && this.steps.lastElementChild === this.lastThought.el) {
      this.lastThought.body.textContent = text;     // шаг уже закрыт ответом — дополняем его, а не плодим новый
      this.addMoreLink(this.lastThought.body);
      return;
    }
    if (!this.thoughtStep) this.appendThought("");
    const { body, el } = this.thoughtStep;
    body.textContent = text;
    el.classList.remove("running");
    el.querySelector(".step-title").firstChild.textContent = "Рассуждение";
    this.addMoreLink(body);
    this.lastThought = this.thoughtStep;
    this.thoughtStep = null;
  }

  // Длинное рассуждение показывается четырьмя строками и раскрывается по кнопке.
  addMoreLink(body) {
    requestAnimationFrame(() => {
      if (body.nextElementSibling?.classList.contains("more-link")) return;
      if (body.scrollHeight <= body.clientHeight + 4) return;
      const more = h("button", { class: "more-link", type: "button", text: "Показать полностью" });
      more.addEventListener("click", () => {
        const open = body.classList.toggle("expanded");
        more.textContent = open ? "Свернуть" : "Показать полностью";
      });
      body.after(more);
    });
  }

  step(kind, iconName, title, ...content) {
    const el = h("div", { class: `step ${kind} running` },
      h("span", { class: "step-icon" }, icon(iconName)),
      h("div", { class: "step-body" }, h("div", { class: "step-title" }, title), ...content));
    this.steps.append(el);
    return el;
  }

  addToolCall(ev) {
    if (this.thoughtStep) this.finishThought(this.thoughtStep.body.textContent);
    this.toolCount += 1;
    this.stepCount += 1;
    const args = Object.entries(ev.args || {})
      .filter(([, v]) => v !== "" && v !== 0 && v !== false && v !== null)
      .map(([k, v]) => h("span", { class: "arg" }, `${ARG_LABELS[k] || k}: `, h("b", { text: v === true ? "да" : String(v) })));
    const result = h("div", { class: "result" }, spinner());
    const el = this.step("tool", TOOL_ICONS[ev.name] || "search", ev.title, h("div", { class: "args" }, ...args), result);
    this.toolSteps.set(ev.id, { el, result, started: performance.now() });
    this.setStatus(`${ev.title}…`);
    this.stick();
  }

  addToolResult(ev) {
    const step = this.toolSteps.get(ev.id);
    if (!step) return;
    step.el.classList.remove("running");
    if (step.started) {
      const ms = Math.round(performance.now() - step.started);
      step.el.querySelector(".step-title").append(h("span", { class: "ms", text: `${ms} мс` }));
    }
    if (!ev.ok) {
      step.el.classList.add("err");
      step.result.replaceChildren(icon("alert"), h("span", { text: ev.summary }));
      step.result.classList.add("err");
      this.setStatus("Инструмент вернул ошибку — агент исправляется…");
      return;
    }
    step.result.replaceChildren(icon("check"), h("span", { text: ev.summary }));
    const places = ev.places || [];
    if (places.length) {
      for (const card of places) if (!this.places.has(card.id)) this.places.set(card.id, card);
      this.lastResult = { places, origin: ev.origin || null };
      const chips = places.slice(0, 8).map((card) => h("button", {
        class: "place-chip", type: "button", text: card.name,
        onclick: () => this.focusPlace(card),
      }));
      step.el.querySelector(".step-body").append(h("div", { class: "result-places" }, ...chips));
      if (!this.replay) this.map.showPlaces(places, { origin: ev.origin });   // карта оживает уже на шаге инструмента
    }
    this.setStatus("Обдумываю результат…");
    this.stick();
  }

  setUsage(ev) {
    this.usage = { ...this.usage, ...ev };
    this.onContext && this.onContext(ev.context_tokens);
  }

  scheduleAnswer() {
    if (this.thoughtStep) this.finishThought(this.thoughtStep.body.textContent);
    this.setStatus("Пишу ответ…");
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => {
      this.renderQueued = false;
      if (this.done) return;            // финальная отрисовка уже сделана в finish()
      this.answer.innerHTML = renderMarkdown(this.answerText);   // разметка экранирована в renderMarkdown
      this.stick();
    });
  }

  showError(message) {
    this.notes.querySelector(".queue-note")?.remove();
    const retry = this.onRetry ? h("button", { class: "btn", type: "button", text: "Повторить", onclick: () => this.onRetry() }) : null;
    this.notes.append(h("div", { class: "error-note", role: "alert" }, icon("alert"), h("span", { text: message }), retry));
  }

  // ---------------------------------------------------------------- завершение

  finish(ev) {
    this.done = true;
    this.stopTimer();
    const elapsed = ev && ev.elapsed_ms ? ev.elapsed_ms : performance.now() - this.started;
    if (ev) this.usage = { ...this.usage, ...ev };
    if (this.thoughtStep) this.finishThought(this.thoughtStep.body.textContent);
    this.trace.querySelectorAll(".step.running").forEach((s) => s.classList.remove("running"));
    this.trace.querySelectorAll(".result .spinner").forEach((s) => s.replaceWith(h("span", { text: "прервано" })));
    this.notes.querySelector(".queue-note")?.remove();
    this.answer.classList.remove("streaming");
    this.answer.innerHTML = renderMarkdown(this.answerText);

    const status = ev && ev.status;
    const parts = [];
    if (status === "cancelled") parts.push("Остановлено");
    else if (status === "error") parts.push("Не удалось ответить");
    else parts.push(`Рассуждал ${fmtSeconds(elapsed)}`);
    if (this.toolCount) parts.push(`${this.toolCount} ${plural(this.toolCount, "инструмент", "инструмента", "инструментов")}`);
    if (this.usage.llm_calls) parts.push(`${this.usage.llm_calls} ${plural(this.usage.llm_calls, "шаг", "шага", "шагов")} модели`);
    this.statusIcon.replaceWith(this.statusIcon = icon(status === "error" ? "alert" : "thought"));
    this.statusText.replaceChildren(h("span", { text: parts.join(" · ") }));
    if (!this.steps.children.length) this.trace.hidden = true;
    this.trace.open = false;

    if (!this.answerText && status === "cancelled") this.answer.replaceChildren(h("p", { class: "muted", text: "Ответ остановлен." }));
    this.renderCards();
    this.linkPlaces();
    this.renderMeta(elapsed);
    this.stick();
  }

  // Карточки: те места, что агент назвал в ответе; если не назвал — последний найденный список.
  cardsToShow() {
    const all = [...this.places.values()];
    const text = norm(this.answerText);
    // «Аптека» без названия совпала бы с любым ответом про аптеки — такие имена не считаем
    const named = all.filter((c) => c.name && norm(c.name) !== norm(c.kind_label) && text.includes(norm(c.name)));
    // в том порядке, в каком агент их назвал: номер карточки = номер в ответе
    if (named.length) return named.sort((a, b) => text.indexOf(norm(a.name)) - text.indexOf(norm(b.name))).slice(0, 10);
    return this.lastResult ? this.lastResult.places.slice(0, 10) : [];
  }

  renderCards() {
    const cards = this.cardsToShow();
    this.shown = cards;
    if (!cards.length) return;
    this.cards.hidden = false;
    this.cards.replaceChildren(...cards.map((card, i) => this.cardEl(card, i + 1)));
    if (!this.replay) this.map.showPlaces(cards, { origin: this.lastResult && this.lastResult.origin });
  }

  showOnMap() {
    if (this.shown && this.shown.length) this.map.showPlaces(this.shown, { origin: this.lastResult && this.lastResult.origin });
  }

  cardEl(card, n) {
    const badges = [];
    if (card.open_now === true) badges.push(h("span", { class: "badge open", text: card.hours_label }));
    else if (card.open_now === false) badges.push(h("span", { class: "badge closed", text: card.hours_label }));
    if (card.distance_m !== null && card.distance_m !== undefined) badges.push(h("span", { class: "badge", text: fmtDistance(card.distance_m) }));
    if (card.found_by) badges.push(h("span", { class: "badge found", text: FOUND_BY[card.found_by] || card.found_by }));
    for (const c of (card.cuisine || []).slice(0, 2)) badges.push(h("span", { class: "badge", text: c }));
    const lines = [];
    if (card.address) lines.push(h("div", { class: "card-line" }, icon("pin"), h("span", { text: card.address })));
    if (card.hours && card.open_now === null) lines.push(h("div", { class: "card-line" }, icon("clock"), h("span", { text: card.hours })));
    if (card.phone) lines.push(h("div", { class: "card-line" }, icon("phone"), h("span", { text: card.phone })));
    const el = h("article", { class: "card", tabindex: "0", "aria-label": card.name },
      h("div", { class: "card-head" }, h("span", { class: "card-num", text: String(n) }),
        h("div", {}, h("div", { class: "card-name", text: card.name }), h("div", { class: "card-kind", text: card.kind_label }))),
      ...lines,
      badges.length ? h("div", { class: "badges" }, ...badges) : null);
    el.addEventListener("mouseenter", () => this.map.highlight(card.id, true));
    el.addEventListener("mouseleave", () => this.map.highlight(card.id, false));
    el.addEventListener("click", () => this.focusPlace(card));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") this.focusPlace(card); });
    card._el = el;
    return el;
  }

  focusPlace(card) {
    if (!this.shown || !this.shown.some((c) => c.id === card.id)) {
      this.map.showPlaces([card], { origin: null });
    } else {
      this.showOnMap();
    }
    this.map.focus(card.id);
    document.dispatchEvent(new CustomEvent("place-focus"));
  }

  // Названия мест в ответе (жирным) становятся ссылками на карту.
  linkPlaces() {
    if (!this.shown || !this.shown.length) return;
    const byName = new Map(this.shown.map((c) => [norm(c.name), c]));
    this.answer.querySelectorAll("strong").forEach((node) => {
      const card = byName.get(norm(node.textContent));
      if (!card) return;
      node.classList.add("place-ref");
      node.title = "Показать на карте";
      node.addEventListener("click", () => this.focusPlace(card));
    });
  }

  renderMeta(elapsed) {
    const u = this.usage;
    const bits = [];
    if (this.model) bits.push(this.model);
    if (u.input_tokens) bits.push(`${fmtTokens(u.input_tokens)} → ${fmtTokens(u.output_tokens)} токенов`);
    if (u.cost_rub) bits.push(`${u.cost_rub.toFixed(2).replace(".", ",")} ₽`);
    if (elapsed) bits.push(fmtSeconds(elapsed));
    if (!bits.length || !this.answerText) return;
    const copy = h("button", { type: "button", text: "Копировать ответ", onclick: async () => {
      try { await navigator.clipboard.writeText(this.answerText); toast("Ответ скопирован"); } catch { toast("Не удалось скопировать", "bad"); }
    } });
    this.meta.hidden = false;
    this.meta.replaceChildren(...bits.map((b) => h("span", { text: b })), copy);
  }

  stick() {
    this.scroller && this.scroller.stick();
  }

  // ---------------------------------------------------------------- история

  static fromHistory(message, opts) {
    const view = new AssistantView(opts);
    view.replay = true;                 // история: карту обновит один раз вызывающий код
    view.started = performance.now();
    for (const ev of message.events) {
      if (ev.type === "tool_result") {
        const step = view.toolSteps.get(ev.id);
        if (step) step.started = null;        // длительность из истории неизвестна — не врём
      }
      view.handle(ev);
    }
    view.answerText = message.text || "";
    if (message.error) view.showError(message.error);
    view.usage = { ...view.usage, ...message.usage };
    view.finishFromHistory();
    view.replay = false;
    return view;
  }

  finishFromHistory() {
    this.finish({ status: "done", elapsed_ms: 0, ...this.usage });
    const parts = [];
    if (this.toolCount) parts.push(`${this.toolCount} ${plural(this.toolCount, "инструмент", "инструмента", "инструментов")}`);
    if (this.usage.llm_calls) parts.push(`${this.usage.llm_calls} ${plural(this.usage.llm_calls, "шаг", "шага", "шагов")} модели`);
    this.statusText.replaceChildren(h("span", { text: parts.length ? `Ход мысли · ${parts.join(" · ")}` : "Ход мысли" }));
    this.meta.querySelectorAll("span").forEach((s) => { if (s.textContent.endsWith(" с")) s.remove(); });
  }
}
