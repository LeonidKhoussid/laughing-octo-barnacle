import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ConsumerInfo, MaskResponse, SpanInfo } from "../lib/types";
import type { JournalEntry } from "../App";

interface Props {
  consumer: string;
  consumerInfo?: ConsumerInfo;
  apiKey: string;
  setApiKey: (k: string) => void;
  addJournal: (e: JournalEntry) => void;
  showToast: (msg: string, error?: boolean) => void;
}

interface Message {
  kind: "user" | "masked" | "provider" | "restored";
  label: string;
  text: string;
  stubNote?: string;
}

const CATEGORY_LABELS: Record<string, string> = {
  FULL_NAME: "Имя", BIRTH_DATE: "Дата рождения", BIRTH_PLACE: "Место рождения",
  PASSPORT: "Паспорт", CITIZENSHIP: "Гражданство", PASSPORT_ISSUER: "Орган выдачи",
  DEPARTMENT_CODE: "Код подразделения", PASSPORT_ISSUE_DATE: "Дата выдачи",
  DRIVER_LICENSE: "Водительское удостоверение", ADDRESS: "Адрес", EMAIL: "email",
  PHONE: "Телефон", INN: "ИНН", CARD: "Карта", CVV: "CVV", PIN: "PIN",
  CARDHOLDER_NAME: "Держатель карты", ACCOUNT_NUMBER: "Счёт",
};

const EXAMPLES = [
  "Клиент Иванов Иван Петрович, телефон +7 918 123-45-67, карта 4276 1234 5678 9012. Почему не прошёл платёж?",
  "Дата рождения: 12 апреля 1990 года. Паспорт серия 0318 номер 123456, выдан ОМВД России по району Тверской.",
  "Email: ivan.petrov@example.com, ИНН 123456789012, адрес: г. Москва, ул. Тверская, д. 15, кв. 42.",
];

// Convert a code-point offset to a UTF-16 index (emoji-safe).
// Used for highlighting sensitive spans in the original text.
function cpToUtf16(str: string, cpOffset: number): number {
  let cp = 0;
  for (let i = 0; i < str.length; i++) {
    if (cp === cpOffset) return i;
    const code = str.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < str.length) {
      const next = str.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) i++;
    }
    cp++;
  }
  return str.length;
}

// Render text with token highlighting (safe: no raw HTML injection).
export function TokenizedText({ text }: { text: string }) {
  const re = /(⟦PII:[^⟧]+⟧)/g;
  const parts: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(
      <span key={key++} className="token">{m[0]}</span>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

// Highlight sensitive spans in the original text using code-point offsets.
function HighlightedOriginal({ text, spans }: { text: string; spans: SpanInfo[] }) {
  if (!spans.length) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  let key = 0;
  for (const s of sorted) {
    const start = cpToUtf16(text, s.start);
    const end = cpToUtf16(text, s.end);
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <span key={key++} className="token">{text.slice(start, end)}</span>,
    );
    cursor = end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

export default function Workspace({
  consumer, consumerInfo, apiKey, setApiKey, addJournal, showToast,
}: Props) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{
    count: number;
    chips: string[];
    timeMs: number | null;
    status: string;
    policy: string;
    requestId: string;
    maskMode: string;
    roundtrip: string;
  } | null>(null);
  const [contextId, setContextId] = useState<string | null>(null);
  const [maskedText, setMaskedText] = useState<string | null>(null);
  const [originalText, setOriginalText] = useState<string | null>(null);
  const [lastChat, setLastChat] = useState<string | null>(null);
  const [spans, setSpans] = useState<SpanInfo[]>([]);
  const requestSeq = useRef(0);

  // Reset request state when the consumer changes.
  useEffect(() => {
    setMessages([]);
    setResult(null);
    setContextId(null);
    setMaskedText(null);
    setOriginalText(null);
    setLastChat(null);
    setSpans([]);
  }, [consumer]);

  const needsAuth = consumerInfo?.authentication !== "competition_exception";

  const doMask = useCallback(async () => {
    if (!input.trim()) {
      showToast("Введите текст", true);
      return;
    }
    if (loading) return;
    const seq = ++requestSeq.current;
    setLoading(true);
    const t0 = performance.now();
    try {
      const data: MaskResponse = await api.mask({ text: input, consumer, apiKey });
      if (seq !== requestSeq.current) return; // stale response
      const dt = performance.now() - t0;
      setContextId(data.context_id);
      setMaskedText(data.masked_text);
      setOriginalText(input);
      setSpans(data.spans || []);
      setMessages((prev) => [
        ...prev,
        { kind: "user", label: "Исходный запрос", text: input },
        { kind: "masked", label: "Замаскированный запрос", text: data.masked_text },
      ]);
      setResult({
        count: (data.spans || []).length,
        chips: Array.from(new Set((data.spans || []).map((s) => s.category))),
        timeMs: dt,
        status: "Успешно",
        policy: data.policy_version,
        requestId: data.context_id,
        maskMode: data.mask_action,
        roundtrip: "—",
      });
      addJournal({
        time: new Date().toISOString(),
        request_id: data.context_id,
        consumer,
        operation: "mask",
        detected: data.detected_counts || {},
        duration_ms: dt,
        outcome: "success",
        policy: data.policy_version,
      });

      if (data.egress_disabled) {
        setMessages((prev) => [
          ...prev,
          {
            kind: "provider",
            label: "Ответ демонстрационного провайдера",
            text: "Отправка модели отключена для этого приложения.",
            stubNote: "Демонстрационный провайдер — без внешнего вызова",
          },
        ]);
        return;
      }
      const chat = await api.chat({
        maskedText: data.masked_text,
        consumer,
        contextId: data.context_id,
        apiKey,
      });
      if (seq !== requestSeq.current) return;
      setLastChat(chat.response);
      setMessages((prev) => [
        ...prev,
        {
          kind: "provider",
          label: "Ответ демонстрационного провайдера",
          text: chat.response,
          stubNote: chat.stub ? "Демонстрационный провайдер — без внешнего вызова" : undefined,
        },
      ]);
    } catch (e) {
      if (seq !== requestSeq.current) return;
      showToast("Ошибка: " + (e as Error).message, true);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [input, consumer, apiKey, loading, showToast, addJournal]);

  const doRestoreRequest = useCallback(async () => {
    if (!contextId || !maskedText) return;
    try {
      const data = await api.unmask({ maskedText, consumer, contextId, apiKey });
      setMessages((prev) => [
        ...prev,
        { kind: "restored", label: "Восстановленный ответ", text: data.original_text },
      ]);
      setResult((prev) => prev ? { ...prev, roundtrip: data.original_text === originalText ? "точно" : "различие" } : prev);
    } catch (e) {
      showToast("Ошибка: " + (e as Error).message, true);
    }
  }, [contextId, maskedText, consumer, apiKey, originalText, showToast]);

  const doRestoreResponse = useCallback(async () => {
    if (!contextId || !lastChat) return;
    try {
      const data = await api.restoreResponse({
        responseText: lastChat,
        consumer,
        contextId,
        apiKey,
      });
      setMessages((prev) => [
        ...prev,
        { kind: "restored", label: "Восстановленный ответ", text: data.restored_text },
      ]);
    } catch (e) {
      showToast("Ошибка: " + (e as Error).message, true);
    }
  }, [contextId, lastChat, consumer, apiKey, showToast]);

  const insertExample = () => {
    const next = EXAMPLES[Math.floor(Math.random() * EXAMPLES.length)];
    setInput((prev) => (prev ? prev + "\n" + next : next));
  };

  const clear = () => {
    setInput("");
    setMessages([]);
    setResult(null);
    setContextId(null);
    setMaskedText(null);
    setOriginalText(null);
    setLastChat(null);
    setSpans([]);
    showToast("Очищено (только вид браузера; серверный контекст сохраняется до TTL)");
  };

  const copyMasked = () => {
    if (maskedText) {
      navigator.clipboard.writeText(maskedText).then(
        () => showToast("Замаскированный текст скопирован"),
        () => showToast("Не удалось скопировать", true),
      );
    }
  };

  return (
    <section className="view active">
      <div className="workspace">
        <div className="conversation-panel">
          {needsAuth && (
            <div className="auth-panel">
              <label>
                API-ключ приложения ({consumer})
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Введите API-ключ"
                  autoComplete="off"
                />
              </label>
            </div>
          )}

          <div className="conversation" aria-live="polite">
            {messages.length === 0 && (
              <div className="empty-state">
                <p>Введите текст с персональными данными и нажмите «Обработать текст».</p>
                <p className="hint">Данные обрабатываются локально; внешняя модель не вызывается (демонстрационный провайдер).</p>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={"msg msg-" + m.kind}>
                <span className="msg-label">{m.label}</span>
                <div className="msg-body">
                  {m.kind === "user" ? (
                    <HighlightedOriginal text={m.text} spans={spans} />
                  ) : m.kind === "masked" || m.kind === "provider" ? (
                    <TokenizedText text={m.text} />
                  ) : (
                    m.text
                  )}
                </div>
                {m.stubNote && <span className="stub-note">{m.stubNote}</span>}
              </div>
            ))}
          </div>

          <div className="composer">
            <label className="sr-only" htmlFor="text-input">Текст запроса</label>
            <textarea
              id="text-input"
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  doMask();
                }
              }}
              placeholder="Введите текст с персональными данными…"
            />
            <div className="composer-actions">
              <div className="composer-left">
                <button className="ghost-btn" onClick={insertExample}>Вставить пример</button>
                <button className="ghost-btn" onClick={clear}>Очистить</button>
                <span className="input-size">{input.length} символов</span>
              </div>
              <div className="composer-right">
                <span className="shortcut-hint">Ctrl/⌘+Enter</span>
                <button className="primary-btn" onClick={doMask} disabled={loading}>
                  {loading ? "Обработка…" : "Обработать текст"}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="result-card">
          <h2>Результат проверки</h2>
          <div className="result-row">
            <span className="result-label">Найдено фрагментов</span>
            <span className="result-value">{result?.count ?? "—"}</span>
          </div>
          <div className="result-chips">
            {(result?.chips || []).map((c) => (
              <span key={c} className="chip">{CATEGORY_LABELS[c] || c}</span>
            ))}
            {result && result.chips.length === 0 && <span className="chip">ПД не найдено</span>}
          </div>
          <div className="result-divider" />
          <div className="result-row">
            <span className="result-label">Время обработки</span>
            <span className="result-value">{result?.timeMs != null ? result.timeMs.toFixed(0) + " мс" : "—"}</span>
          </div>
          <div className="result-row">
            <span className="result-label">Статус</span>
            <span className="result-value">{result?.status ?? "—"}</span>
          </div>
          <details className="result-details">
            <summary>Подробнее</summary>
            <div className="result-detail-line">Политика: {result?.policy ?? "—"}</div>
            <div className="result-detail-line">Запрос: {result?.requestId ?? "—"}</div>
            <div className="result-detail-line">Режим маски: {result?.maskMode ?? "—"}</div>
            <div className="result-detail-line">Round-trip: {result?.roundtrip ?? "—"}</div>
            <div className="result-detail-line">Эталонная разметка не задана</div>
          </details>
          <div className="actions">
            {maskedText && (
              <button className="ghost-btn" onClick={copyMasked}>Копировать маску</button>
            )}
            {maskedText && consumerInfo?.allow_unmask && (
              <button className="ghost-btn" onClick={doRestoreRequest}>Демаскировать запрос</button>
            )}
            {lastChat && consumerInfo?.allow_unmask && (
              <button className="ghost-btn" onClick={doRestoreResponse}>Демаскировать ответ</button>
            )}
            {maskedText && !consumerInfo?.allow_unmask && (
              <span className="badge badge-warn">Демаскирование запрещено</span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
