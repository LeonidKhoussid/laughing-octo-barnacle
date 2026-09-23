"use strict";

// AlfaGen PII Gateway demo UI (designer layout).
// Security: all untrusted text rendered via textContent / DOM nodes.
// NEVER use innerHTML with user-provided text (no XSS, R53).

const $ = (id) => document.getElementById(id);

const state = {
  consumer: "support_demo",
  contextId: null,
  maskedText: null,
  originalText: null,
  spans: [],
  policyVersion: null,
  maskAction: null,
  apiKey: null,
  lastTrustA: null,
  journal: [],
  consumers: [],
};

const CATEGORY_LABELS = {
  FULL_NAME: "Имя", BIRTH_DATE: "Дата рождения", BIRTH_PLACE: "Место рождения",
  PASSPORT: "Паспорт", CITIZENSHIP: "Гражданство", PASSPORT_ISSUER: "Орган выдачи",
  DEPARTMENT_CODE: "Код подразделения", PASSPORT_ISSUE_DATE: "Дата выдачи",
  DRIVER_LICENSE: "Водительское удостоверение", ADDRESS: "Адрес", EMAIL: "email",
  PHONE: "Телефон", INN: "ИНН", CARD: "Карта", CVV: "CVV", PIN: "PIN",
  CARDHOLDER_NAME: "Держатель карты", ACCOUNT_NUMBER: "Счёт",
};
const DEFAULT_LABEL = "Фрагмент";

function catLabel(cat) { return CATEGORY_LABELS[cat] || DEFAULT_LABEL; }

// ---- Toast / errors --------------------------------------------------------

function toast(msg, isError) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = "toast"; }, 3200);
}

// ---- API -------------------------------------------------------------------

async function api(path, body, extraHeaders) {
  const headers = { "Content-Type": "application/json" };
  if (state.apiKey) headers["X-API-Key"] = state.apiKey;
  if (extraHeaders) Object.assign(headers, extraHeaders);
  const resp = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = (data && data.detail && data.detail.message) || ("HTTP " + resp.status);
    const err = new Error(msg);
    err.status = resp.status;
    throw err;
  }
  return data;
}

// ---- Code-point -> UTF-16 conversion (emoji fix) ---------------------------

// The backend reports spans in Unicode code-point offsets. JS strings are UTF-16.
// Convert a code-point offset to a UTF-16 index so String.slice works correctly
// when emoji / non-BMP chars precede a sensitive span (section 7.3).
function cpToUtf16(str, cpOffset) {
  let cp = 0;
  for (let i = 0; i < str.length; i++) {
    if (cp === cpOffset) return i;
    const code = str.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < str.length) {
      const next = str.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) { i++; }
    }
    cp++;
  }
  return str.length;
}

// ---- Conversation rendering ------------------------------------------------

function addMessage(kind, label, text, opts) {
  const conv = $("conversation");
  const empty = $("empty-state");
  if (empty) empty.remove();
  const msg = document.createElement("div");
  msg.className = "msg msg-" + kind;
  if (label) {
    const lab = document.createElement("span");
    lab.className = "msg-label";
    lab.textContent = label;
    msg.appendChild(lab);
  }
  const body = document.createElement("div");
  body.className = "msg-body";
  // Render tokens with highlighting, everything else as text (no innerHTML).
  if (opts && opts.highlightTokens) {
    renderTokenized(body, text);
  } else {
    body.textContent = text;
  }
  msg.appendChild(body);
  if (opts && opts.stubNote) {
    const note = document.createElement("span");
    note.className = "stub-note";
    note.textContent = opts.stubNote;
    msg.appendChild(note);
  }
  conv.appendChild(msg);
  conv.scrollTop = conv.scrollHeight;
}

function renderTokenized(container, text) {
  // Split on ⟦PII:...⟧ tokens and render them as styled spans.
  const re = /(⟦PII:[^⟧]+⟧)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) container.appendChild(document.createTextNode(text.slice(last, m.index)));
    const tok = document.createElement("span");
    tok.className = "token";
    tok.textContent = m[0];
    container.appendChild(tok);
    last = m.index + m[0].length;
  }
  if (last < text.length) container.appendChild(document.createTextNode(text.slice(last)));
}

// ---- Result summary --------------------------------------------------------

function updateResult(data, elapsedMs) {
  const spans = data.spans || [];
  $("result-count").textContent = String(spans.length);
  $("result-time").textContent = elapsedMs != null ? elapsedMs.toFixed(0) + " мс" : "—";
  $("result-status").textContent = "Успешно";
  $("result-policy").textContent = state.policyVersion || "—";
  $("result-request-id").textContent = data.context_id || "—";
  $("result-mask-mode").textContent = data.mask_action || "—";
  $("result-roundtrip").textContent = "—";
  const chips = $("result-chips");
  chips.textContent = "";
  const seen = new Set();
  spans.forEach((s) => {
    if (!seen.has(s.category)) {
      seen.add(s.category);
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = catLabel(s.category);
      chips.appendChild(chip);
    }
  });
  if (!spans.length) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = "ПД не найдено";
    chips.appendChild(chip);
  }
}

// ---- Main processing flow --------------------------------------------------

async function doMask() {
  const text = $("text-input").value;
  if (!text) { toast("Введите текст", true); return; }
  const btn = $("btn-mask");
  btn.disabled = true;
  btn.textContent = "Обработка…";
  const t0 = performance.now();
  try {
    const data = await api("/demo/mask", { text, consumer: state.consumer });
    const dt = performance.now() - t0;
    state.contextId = data.context_id;
    state.maskedText = data.masked_text;
    state.originalText = text;
    state.spans = data.spans || [];
    state.maskAction = data.mask_action;

    addMessage("user", "Исходный запрос", text);
    addMessage("masked", "Замаскированный запрос", data.masked_text, { highlightTokens: true });
    updateResult(data, dt);
    addJournal("mask", data);

    // If egress is disabled for this consumer, do not call the provider.
    if (data.egress_disabled) {
      addMessage("provider", "Ответ демонстрационного провайдера",
        "Отправка модели отключена для этого приложения.", { stubNote: "Демонстрационный провайдер — без внешнего вызова" });
      showRestoreButtons();
      return;
    }
    // Call the provider (stub) with the masked payload.
    const chat = await api("/demo/chat", {
      masked_text: data.masked_text,
      consumer: state.consumer,
      context_id: data.context_id,
    });
    addMessage("provider", "Ответ демонстрационного провайдера", chat.response, {
      highlightTokens: true,
      stubNote: "Демонстрационный провайдер — без внешнего вызова",
    });
    state.lastChat = chat;
    showRestoreButtons();
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Обработать текст";
  }
}

async function doRestore() {
  if (!state.contextId || !state.maskedText) { toast("Сначала обработайте текст", true); return; }
  try {
    const data = await api("/demo/unmask", {
      masked_text: state.maskedText,
      consumer: state.consumer,
      context_id: state.contextId,
    });
    addMessage("restored", "Восстановленный ответ", data.original_text);
    $("result-roundtrip").textContent = data.original_text === state.originalText ? "точно" : "различие";
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

async function doRestoreResponse() {
  if (!state.contextId || !state.lastChat) { toast("Сначала получите ответ провайдера", true); return; }
  try {
    const data = await api("/demo/restore-response", {
      response_text: state.lastChat.response,
      consumer: state.consumer,
      context_id: state.contextId,
    });
    addMessage("restored", "Восстановленный ответ", data.restored_text);
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

// ---- Journal ---------------------------------------------------------------

function addJournal(op, data) {
  const entry = {
    time: new Date().toISOString(),
    request_id: data.context_id || "—",
    consumer: state.consumer,
    operation: op,
    detected: data.detected_counts || {},
    duration_ms: null,
    outcome: "success",
    policy: state.policyVersion || "—",
  };
  state.journal.unshift(entry);
  if (state.journal.length > 50) state.journal.pop();
  renderJournal();
}

function renderJournal() {
  const list = $("journal-list");
  list.textContent = "";
  if (!state.journal.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Журнал пуст. Выполните обработку текста, чтобы увидеть безопасные записи.";
    list.appendChild(empty);
    return;
  }
  state.journal.forEach((e) => {
    const row = document.createElement("div");
    row.className = "journal-entry";
    const fields = [
      ["Время", e.time], ["Запрос", e.request_id], ["Приложение", e.consumer],
      ["Операция", e.operation], ["ПД", Object.entries(e.detected).map(([k, v]) => k + ":" + v).join(", ") || "нет"],
      ["Статус", e.outcome], ["Политика", e.policy],
    ];
    fields.forEach(([label, value]) => {
      const f = document.createElement("span");
      f.className = "j-field";
      const l = document.createElement("span");
      l.className = "j-label";
      l.textContent = label + ": ";
      f.appendChild(l);
      f.appendChild(document.createTextNode(String(value)));
      row.appendChild(f);
    });
    list.appendChild(row);
  });
}

// ---- Apps view -------------------------------------------------------------

async function loadApps() {
  const list = $("apps-list");
  list.textContent = "";
  try {
    const resp = await fetch("/config");
    const cfg = await resp.json();
    const consumers = cfg.consumers || [];
    state.consumers = consumers;
    consumers.forEach((name) => {
      const card = document.createElement("div");
      card.className = "app-card";
      const h = document.createElement("h3");
      h.textContent = name;
      card.appendChild(h);
      const info = cfg.consumer_info && cfg.consumer_info[name];
      if (info) {
        // Editable controls (server-side validated via /config/update).
        const form = document.createElement("div");
        form.className = "app-form";

        const mkCheck = (label, key, value) => {
          const row = document.createElement("label");
          row.className = "app-check";
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = !!value;
          cb.dataset.key = key;
          row.appendChild(cb);
          row.appendChild(document.createTextNode(" " + label));
          return row;
        };

        form.appendChild(mkCheck("Включено", "enabled", info.enabled));
        form.appendChild(mkCheck("Демаскирование", "allow_unmask", info.allow_unmask));
        form.appendChild(mkCheck("Egress (вызов модели)", "allow_llm_egress", info.allow_llm_egress));

        const maskRow = document.createElement("label");
        maskRow.className = "app-check";
        maskRow.textContent = "Маска: ";
        const maskSel = document.createElement("select");
        maskSel.dataset.key = "default_action";
        ["tokenize_full", "opaque_token_full"].forEach((v) => {
          const o = document.createElement("option");
          o.value = v;
          o.textContent = v;
          if (v === info.mask_action) o.selected = true;
          maskSel.appendChild(o);
        });
        maskRow.appendChild(maskSel);
        form.appendChild(maskRow);

        const typesRow = document.createElement("label");
        typesRow.className = "app-check";
        typesRow.textContent = "Типы: ";
        const typesInput = document.createElement("input");
        typesInput.type = "text";
        typesInput.value = info.detect_types || "all_required";
        typesInput.dataset.key = "detect_types";
        typesRow.appendChild(typesInput);
        form.appendChild(typesRow);

        const saveBtn = document.createElement("button");
        saveBtn.className = "primary-btn";
        saveBtn.textContent = "Сохранить";
        saveBtn.addEventListener("click", () => saveConsumerConfig(name, form, saveBtn));
        form.appendChild(saveBtn);

        card.appendChild(form);
      }
      list.appendChild(card);
    });
  } catch (e) {
    toast("Не удалось загрузить приложения: " + e.message, true);
  }
}

async function saveConsumerConfig(name, form, btn) {
  const updates = {};
  form.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    updates[cb.dataset.key] = cb.checked;
  });
  form.querySelectorAll("select[data-key]").forEach((sel) => {
    updates[sel.dataset.key] = sel.value;
  });
  form.querySelectorAll("input[type=text][data-key]").forEach((inp) => {
    updates[inp.dataset.key] = inp.value;
  });
  const adminKey = prompt("Введите ключ обновления конфигурации (PII_CONFIG_UPDATE_KEY):");
  if (!adminKey) { toast("Обновление отменено", true); return; }
  btn.disabled = true;
  btn.textContent = "Сохранение…";
  try {
    const resp = await fetch("/config/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ consumer: name, updates, admin_key: adminKey }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      const msg = (data.detail && data.detail.message) || ("HTTP " + resp.status);
      toast("Ошибка: " + msg, true);
      return;
    }
    toast("Конфигурация сохранена: " + name);
    loadApps();
    loadConfig();
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Сохранить";
  }
}

// ---- Config / consumers ----------------------------------------------------

async function loadConfig() {
  try {
    const resp = await fetch("/config");
    const cfg = await resp.json();
    state.policyVersion = cfg.policy_version;
    $("policy-version").textContent = "Политика: " + cfg.policy_version;
    const sel = $("consumer");
    sel.textContent = "";
    (cfg.consumers || []).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    sel.value = state.consumer;
    sel.addEventListener("change", () => {
      state.consumer = sel.value;
      state.contextId = null;
      state.maskedText = null;
      state.originalText = null;
      state.spans = [];
      state.apiKey = null;
      resetWorkspace();
    });
    loadApps();
  } catch (e) {
    toast("Не удалось загрузить конфигурацию: " + e.message, true);
  }
}

function resetWorkspace() {
  const conv = $("conversation");
  conv.textContent = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  const p1 = document.createElement("p");
  p1.textContent = "Введите текст с персональными данными и нажмите «Обработать текст».";
  const p2 = document.createElement("p");
  p2.className = "hint";
  p2.textContent = "Данные обрабатываются локально; внешняя модель не вызывается (демонстрационный провайдер).";
  empty.appendChild(p1);
  empty.appendChild(p2);
  empty.id = "empty-state";
  conv.appendChild(empty);
  $("result-count").textContent = "—";
  $("result-time").textContent = "—";
  $("result-status").textContent = "—";
  $("result-chips").textContent = "";
  $("result-policy").textContent = "—";
  $("result-request-id").textContent = "—";
  $("result-mask-mode").textContent = "—";
  $("result-roundtrip").textContent = "—";
}

// ---- Navigation ------------------------------------------------------------

function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.classList.toggle("active", n.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === "view-" + name);
  });
  const titles = { workspace: "Обработка текста", apps: "Приложения", journal: "Журнал" };
  $("view-title").textContent = titles[name] || "Обработка текста";
  if (name === "journal") renderJournal();
}

// ---- Trust Lab -------------------------------------------------------------

function setPre(id, value) {
  $(id).textContent = value == null ? "—" : String(value);
}

function renderVariations(variations) {
  if (!variations || !variations.length) return "Вариации не запущены.";
  return variations.map((v) => {
    const rt = v.exact_round_trip ? "round-trip OK" : "round-trip FAIL";
    const missed = v.missed.length ? " пропущено=" + v.missed.length : "";
    const over = v.overmasked.length ? " лишних=" + v.overmasked.length : "";
    return "[" + v.name + "] " + v.status.toUpperCase() + " · " + rt + missed + over +
      " · " + v.time_seconds.toFixed(4) + "s";
  }).join("\n");
}

async function doTrustA(runVariations) {
  const text = $("trust-a-text").value;
  if (!text) { toast("Введите текст для проверки", true); return; }
  const labeled = $("trust-a-labeled").checked;
  try {
    const d = await api("/trust-lab/action-a", {
      text, consumer: state.consumer, labeled,
      seed: 20260922, run_variations: runVariations && labeled,
    });
    state.lastTrustA = d;
    const rt = d.exact_round_trip ? "passed" : "FAILED";
    let out = "Round-trip: " + rt + "\n";
    out += "Обнаружено: " + JSON.stringify(d.detected_counts) + "\n";
    out += "Замаскировано: " + JSON.stringify(d.masked_counts) + "\n";
    out += "Версия политики: " + d.policy_version + "\n";
    out += "Время: " + d.timings.map((t) => t.stage + "=" + t.seconds.toFixed(4) + "s").join(", ") + "\n";
    if (d.note) out += "Примечание: " + d.note + "\n";
    if (d.reasons && d.reasons.length) {
      out += "Основания решений:\n" + d.reasons.map((r) =>
        "  " + r.category + " · detector=" + r.detector + (r.rule ? " · rule=" + r.rule : "")
      ).join("\n") + "\n";
    }
    out += "\nЗамаскированный текст:\n" + d.masked_text + "\n";
    if (d.variations && d.variations.length) {
      out += "\nВариации (реальные результаты):\n" + renderVariations(d.variations) + "\n";
    }
    setPre("trust-a-output", out);
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

async function doTrustB() {
  const consumer = $("trust-b-consumer").value || state.consumer;
  const action = $("trust-b-action").value;
  const addDetector = $("trust-b-add-detector").checked;
  const candidate = {};
  if (action) candidate.default_action = action;
  if (addDetector) candidate.add_detector = true;
  try {
    const d = await api("/trust-lab/action-b", { consumer, candidate });
    const a = d.active, c = d.candidate;
    let out = "Активная: " + a.mask_action + " · пропуски=" + a.missed_entities +
      " · лишние=" + a.overmasked_entities + " · round-trip fail=" + a.round_trip_failures + "\n";
    out += "Кандидат: " + c.mask_action + " · пропуски=" + c.missed_entities +
      " · лишние=" + c.overmasked_entities + " · round-trip fail=" + c.round_trip_failures + "\n";
    out += "\nИзменение: пропуски=" + (d.delta_missed > 0 ? "+" : "") + d.delta_missed +
      " · лишние=" + (d.delta_overmasked > 0 ? "+" : "") + d.delta_overmasked +
      " · round-trip=" + (d.delta_round_trip_failures > 0 ? "+" : "") + d.delta_round_trip_failures + "\n";
    if (d.regressions.length) out += "\nРЕГРЕССИИ:\n" + d.regressions.map((r) => "  ✗ " + r).join("\n") + "\n";
    else out += "\nРегрессий не выявлено на проверенном наборе.\n";
    if (d.improvements.length) out += "Улучшения:\n" + d.improvements.map((r) => "  ✓ " + r).join("\n") + "\n";
    out += "\n" + d.note;
    setPre("trust-b-output", out);
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

async function doTrustC() {
  const adminKey = $("trust-c-admin").value;
  if (!adminKey) { toast("Введите admin-ключ", true); return; }
  const faultType = $("trust-c-fault").value;
  const text = $("trust-a-text").value || "Клиент Иван Петров, email ivan@example.com";
  try {
    const d = await api("/trust-lab/action-c", {
      consumer: state.consumer, text, fault_type: faultType, detector_id: "full_name",
    }, { "X-Admin-Key": adminKey });
    let out = "Статус: " + d.status + "\n";
    if (d.reason) out += "Причина: " + d.reason + "\n";
    out += "Вызовов транспортного адаптера LLM в этом сценарии: " + d.upstream_calls + "\n";
    out += "Исходный запрос наружу не отправлялся этим адаптером: " + (d.request_sent_out ? "нет" : "да") + "\n";
    if (d.degraded_components && d.degraded_components.length) {
      out += "Деградировавшие компоненты: " + d.degraded_components.join(", ") + "\n";
    }
    setPre("trust-c-output", out);
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

async function doTrustCRecover() {
  const adminKey = $("trust-c-admin").value;
  if (!adminKey) { toast("Введите admin-ключ", true); return; }
  try {
    const d = await api("/trust-lab/action-c/recover", {}, { "X-Admin-Key": adminKey });
    setPre("trust-c-output", "Статус: " + d.status + "\nДеградировавшие компоненты: " +
      (d.degraded_components.length ? d.degraded_components.join(", ") : "нет"));
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

async function doTrustReport() {
  const a = state.lastTrustA;
  const body = {
    dataset_or_scenario_id: a && a.labeled ? "trust-lab-action-a" : null,
    seed: 20260922,
    input_length: a ? a.text.length : null,
    detected_counts: a ? a.detected_counts : {},
    masked_counts: a ? a.masked_counts : {},
    exact_round_trip: a ? (a.exact_round_trip ? "passed" : "failed") : "not_tested",
    upstream_calls: 0,
    timings: a ? Object.fromEntries(a.timings.map((t) => [t.stage, t.seconds])) : {},
    degraded_components: [],
    known_limitations: [],
    include_synthetic: false,
  };
  try {
    const d = await api("/trust-lab/report", body);
    setPre("trust-report-output", JSON.stringify(d, null, 2));
  } catch (e) {
    toast("Ошибка: " + e.message, true);
  }
}

// ---- Composer helpers ------------------------------------------------------

function insertExample() {
  const examples = [
    "Клиент Иванов Иван Петрович, телефон +7 918 123-45-67, карта 4276 1234 5678 9012. Почему не прошёл платёж?",
    "Дата рождения: 12 апреля 1990 года. Паспорт серия 0318 номер 123456, выдан ОМВД России по району Тверской.",
    "Email: ivan.petrov@example.com, ИНН 123456789012, адрес: г. Москва, ул. Тверская, д. 15, кв. 42.",
  ];
  const current = $("text-input").value;
  const next = examples[Math.floor(Math.random() * examples.length)];
  $("text-input").value = current ? current + "\n" + next : next;
  updateInputSize();
}

function updateInputSize() {
  const len = $("text-input").value.length;
  $("input-size").textContent = len + " символов";
}

function doClear() {
  state.contextId = null;
  state.maskedText = null;
  state.originalText = null;
  state.spans = [];
  $("text-input").value = "";
  updateInputSize();
  resetWorkspace();
  toast("Очищено (только вид браузера; серверный контекст сохраняется до TTL)");
}

// ---- Init ------------------------------------------------------------------

function initTrustLab() {
  $("btn-trust-a").addEventListener("click", () => doTrustA(false));
  $("btn-trust-a-variations").addEventListener("click", () => doTrustA(true));
  $("btn-trust-b").addEventListener("click", doTrustB);
  $("btn-trust-c").addEventListener("click", doTrustC);
  $("btn-trust-c-recover").addEventListener("click", doTrustCRecover);
  $("btn-trust-report").addEventListener("click", doTrustReport);
  document.querySelectorAll(".trust-tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".trust-tab").forEach((x) => x.classList.toggle("active", x === t));
      document.querySelectorAll(".trust-panel").forEach((p) => {
        p.classList.toggle("active", p.id === "trust-" + t.dataset.trust);
      });
    });
  });
  const sel = $("trust-b-consumer");
  sel.textContent = "";
  (state.consumers || []).forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
  sel.value = state.consumer;
}

function showRestoreButtons() {
  if (state._restoreBtn) state._restoreBtn.style.display = "inline-block";
  if (state._restoreRespBtn) state._restoreRespBtn.style.display = "inline-block";
}

function init() {
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.addEventListener("click", () => switchView(n.dataset.view));
  });
  $("btn-mask").addEventListener("click", doMask);
  $("btn-example").addEventListener("click", insertExample);
  $("btn-clear").addEventListener("click", doClear);
  $("text-input").addEventListener("input", updateInputSize);
  $("text-input").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); doMask(); }
  });
  $("btn-settings").addEventListener("click", () => switchView("apps"));
  // Restore buttons appear after a provider response.
  const conv = $("conversation");
  const restoreBtn = document.createElement("button");
  restoreBtn.className = "ghost-btn";
  restoreBtn.textContent = "Демаскировать запрос";
  restoreBtn.style.display = "none";
  restoreBtn.addEventListener("click", doRestore);
  conv.appendChild(restoreBtn);
  state._restoreBtn = restoreBtn;
  const restoreRespBtn = document.createElement("button");
  restoreRespBtn.className = "ghost-btn";
  restoreRespBtn.textContent = "Демаскировать ответ";
  restoreRespBtn.style.display = "none";
  restoreRespBtn.addEventListener("click", doRestoreResponse);
  conv.appendChild(restoreRespBtn);
  state._restoreRespBtn = restoreRespBtn;
  initTrustLab();
  loadConfig();
}

document.addEventListener("DOMContentLoaded", init);