import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ContextCheckResponse, DecisionInfo, MaskResponse } from "../lib/types";
import { ApiErrorImpl } from "../lib/types";
import type { JournalEntry } from "../App";
import { TokenizedText } from "./Workspace";

const EXAMPLE = "Клиент Иванов Иван Петрович, телефон +7 918 123-45-67, карта 4276 1234 5678 9012. Почему не прошёл платёж?";
const EXAMPLES = [
  { label: "Данные клиента", text: EXAMPLE },
  { label: "Публичная личность", text: "Поэт Александр Сергеевич Пушкин написал роман «Евгений Онегин»." },
  { label: "Клиент с тем же именем", text: "Клиент Александр Сергеевич Пушкин, телефон +7 918 123-45-67, карта 4276 1234 5678 9012. Почему не прошёл платёж?" },
  { label: "Смешанный контекст", text: "Клиент Иван Иванович Петров читает произведения Александра Сергеевича Пушкина. Телефон клиента: +7 918 123-45-67." },
  { label: "Наука и личные данные", text: "Альберт Эйнштейн разработал теорию относительности. Клиент Азамат Нурмагомедов оставил телефон +7 903 123-45-67." },
  { label: "Литература", text: "В романе «Анна Каренина» Алексей Александрович Каренин — вымышленный персонаж." },
];

const CONTEXT_NAME = "Бернард Шоу";
const CONTEXT_PAIR = [
  { label: "Публичное упоминание", text: "Драматург Бернард Шоу написал «Пигмалион»." },
  { label: "Личная запись", text: "Мой знакомый Бернард Шоу прислал личное сообщение." },
];
const EVIDENCE_LABELS: Record<string, string> = {
  local_bert_person: "Локальная NER-модель распознала имя",
  local_nli_public_context: "Локальная NLI-модель подтвердила публичный контекст",
  local_nli_private_context: "Локальная NLI-модель распознала личную информацию",
  historical_public_reference: "Сработало правило исторического упоминания",
  institution_dedication: "Имя входит в название учреждения",
  private_record_guard: "Обнаружен контекст личной записи",
  context_uncertain_protected: "Публичный контекст не подтверждён: имя защищено",
  personal_context: "Обнаружен признак личных данных",
  name_grammar: "Имя распознано по структуре",
};

function nameDecisions(result: ContextCheckResponse): DecisionInfo[] {
  const start = Array.from(result.text.slice(0, result.text.indexOf(CONTEXT_NAME))).length;
  const end = start + Array.from(CONTEXT_NAME).length;
  return result.decisions.filter((decision) => decision.category === "FULL_NAME"
    && [...decision.evidence_spans, ...decision.sensitive_spans]
      .some((span) => span.start <= start && span.end >= end));
}

function nameOutcome(result: ContextCheckResponse): string {
  const start = Array.from(result.text.slice(0, result.text.indexOf(CONTEXT_NAME))).length;
  const end = start + Array.from(CONTEXT_NAME).length;
  if (result.spans.some((span) => span.start <= start && span.end >= end)) return "Имя скрыто";
  if (result.spans.some((span) => span.start < end && span.end > start)) return "Имя скрыто частично";
  if (nameDecisions(result).some((decision) => decision.decision === "KEEP")) return "Имя сохранено";
  return "Нет решения о сохранении имени";
}

function ContextComparison() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<ContextCheckResponse[] | null>(null);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, []);

  async function compare() {
    if (loading) return;
    const current = ++sequence.current;
    setLoading(true);
    setError("");
    setResults(null);
    try {
      const checked = await Promise.all(CONTEXT_PAIR.map((example) => api.checkContext(example.text)));
      if (current !== sequence.current) return;
      if (checked.some((result) => !Array.isArray(result.decisions) || !Array.isArray(result.spans))) {
        setError("Сервис не вернул объяснение решений. Обновите сервис и повторите проверку.");
        return;
      }
      if (checked.some((result, index) => result.text !== CONTEXT_PAIR[index].text
        || result.restored !== CONTEXT_PAIR[index].text || !result.exact_round_trip)) {
        setError("Проверка не пройдена: исходный или восстановленный текст не совпадает с примером.");
        return;
      }
      setResults(checked);
    } catch {
      if (current === sequence.current) setError("Не удалось сравнить контексты. Повторите проверку.");
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }

  return <details className="context-comparison">
    <summary>Одно имя — два контекста</summary>
    <p>Сравните решения для одного имени в рассказе о литературе и в личной записи. Личная запись вымышлена.</p>
    <button className="ghost-btn" type="button" disabled={loading} onClick={() => void compare()}>
      {loading ? "Сравниваем…" : "Сравнить контексты"}
    </button>
    <p className="context-status" role="status" aria-live="polite">
      {loading ? "Проверяем оба текста локально…" : results ? "Оба текста восстановлены без изменений." : ""}
    </p>
    {error && <p className="judge-error" role="alert">{error}</p>}
    <div className="judge-results context-results">
      {CONTEXT_PAIR.map((example, index) => <section className="judge-result" key={example.label}>
        <h3>{example.label}</h3>
        <p className="judge-output">{example.text}</p>
        {results && <>
          <p className="context-outcome">{nameOutcome(results[index])}</p>
          <div className="judge-output"><TokenizedText text={results[index].masked_text} /></div>
        </>}
      </section>)}
    </div>
    {results && <details className="context-evidence">
      <summary>Почему приняты эти решения</summary>
      {results.map((result, index) => <section key={CONTEXT_PAIR[index].label}>
        <h4>{CONTEXT_PAIR[index].label}</h4>
        {nameDecisions(result).length === 0 ? <p>Движок не вернул решение для этого имени.</p>
          : nameDecisions(result).map((decision, decisionIndex) => <div key={decisionIndex}>
            <p><strong>{decision.decision === "KEEP" ? "Сохранить" : decision.decision === "MASK" ? "Скрыть" : "Неопределённость"}</strong></p>
            <ul>{decision.signals.map((signal, signalIndex) => <li key={signalIndex}>
              {EVIDENCE_LABELS[signal] ?? signal}
            </li>)}</ul>
            <p className="context-rule">{decision.detector_id} · {decision.rule_id}</p>
          </div>)}
      </section>)}
      <p className="judge-caption">Показаны решения и признаки текущего запуска. Эти два примера не измеряют точность на других текстах.</p>
    </details>}
  </details>;
}

export default function JudgeDemo({ addJournal }: {
  addJournal: (entry: JournalEntry) => void;
}) {
  const [input, setInput] = useState(EXAMPLE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{
    original: string;
    mask: MaskResponse;
    restored: string | null;
    duration: number;
  } | null>(null);
  const sequence = useRef(0);

  useEffect(() => () => { sequence.current++; }, []);

  function changeInput(text: string) {
    sequence.current++;
    setInput(text);
    setResult(null);
    setError("");
    setLoading(false);
  }

  async function runDemo() {
    if (loading || !input.trim()) return;
    const current = ++sequence.current;
    const original = input;
    const started = performance.now();
    setLoading(true);
    setResult(null);
    setError("");
    try {
      const mask = await api.mask({ text: original, consumer: "autocheck" });
      if (current !== sequence.current) return;
      setResult({ original, mask, restored: null, duration: performance.now() - started });
      addJournal({
        time: new Date().toISOString(), request_id: mask.context_id,
        consumer: "autocheck", operation: "mask", detected: mask.detected_counts,
        duration_ms: performance.now() - started, outcome: "success", policy: mask.policy_version,
      });
      const restoreStarted = performance.now();
      const restored = await api.unmask({
        maskedText: mask.masked_text, consumer: "autocheck", contextId: mask.context_id,
      });
      if (current !== sequence.current) return;
      const exact = restored.original_text === original;
      setResult({ original, mask, restored: restored.original_text, duration: performance.now() - started });
      if (!exact) setError("Восстановленный текст отличается от исходного. Проверка не пройдена.");
      addJournal({
        time: new Date().toISOString(), request_id: mask.context_id,
        consumer: "autocheck", operation: "unmask", detected: {},
        duration_ms: performance.now() - restoreStarted,
        outcome: exact ? "success" : "mismatch", policy: restored.policy_version,
      });
    } catch (e) {
      if (current === sequence.current) {
        setError(e instanceof ApiErrorImpl && e.status === 413
          ? "Текст слишком большой. Сократите его и повторите проверку."
          : "Не удалось завершить проверку. Попробуйте ещё раз или проверьте доступность сервиса.");
      }
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }

  const complete = result !== null && result.restored !== null;
  const exact = complete && result.restored === result.original;

  return (
    <main className="judge-demo">
      <div className="judge-intro">
        <h2>Убедитесь, что данные скрыты и восстановлены</h2>
        <p>Пример уже заполнен. Нажмите «Проверить» или введите свой текст.</p>
        <p className="judge-local">Публичные упоминания сохраняются, личные записи защищаются. Проверьте разницу на примерах.</p>
        <p className="judge-local">Без ключа и настроек. Проверка выполняется локально, без вызова LLM.</p>
      </div>

      <form className="judge-input composer" onSubmit={(e) => { e.preventDefault(); void runDemo(); }}>
        <label className="judge-example" htmlFor="judge-example">Пример
          <select id="judge-example" value={EXAMPLES.findIndex((example) => example.text === input)}
            onChange={(e) => changeInput(EXAMPLES[Number(e.target.value)].text)}>
            {!EXAMPLES.some((example) => example.text === input) && <option value={-1} disabled>Свой текст</option>}
            {EXAMPLES.map((example, index) => <option key={example.label} value={index}>{example.label}</option>)}
          </select>
        </label>
        <label htmlFor="judge-input"><strong>Исходный текст</strong><span>Личные данные в примерах вымышлены</span></label>
        <textarea id="judge-input" value={input} rows={4} disabled={loading}
          onChange={(e) => changeInput(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void runDemo(); }
          }} />
        <div className="composer-actions">
          <button className="ghost-btn" type="button" onClick={() => changeInput(EXAMPLE)}>Вернуть пример</button>
          <button className="primary-btn" type="submit" disabled={loading || !input.trim()}>
            {loading ? "Проверяем…" : "Проверить"}
          </button>
        </div>
      </form>

      {error && <p className="judge-error" role="alert">{error}</p>}
      <div className="judge-status" role="status" aria-live="polite">
        {loading && "Скрываем данные и проверяем восстановление…"}
        {exact && !loading && <>
          <strong>Текст восстановлен без изменений</strong>
          <span>Найдено фрагментов: {result.mask.spans.length} · Полный цикл: {Math.round(result.duration)} мс</span>
        </>}
      </div>

      {result && <div className="judge-results">
        <section className="judge-result" aria-labelledby="masked-title">
          <h2 id="masked-title">После маскирования</h2>
          <p className="judge-caption">{result.mask.spans.length ? "Найденные данные заменены непрозрачными токенами." : "Персональные данные не обнаружены. Текст не изменён."}</p>
          <div className="judge-output"><TokenizedText text={result.mask.masked_text} /></div>
        </section>
        <section className="judge-result" aria-labelledby="restored-title">
          <h2 id="restored-title">После восстановления</h2>
          <p className="judge-caption">{exact ? "Точное совпадение с исходным текстом, включая пробелы." : complete ? "Есть отличия от исходного текста." : "Результат появится после завершения проверки."}</p>
          <div className="judge-output">{result.restored ?? (loading ? "Восстанавливаем…" : "Восстановление не завершено.")}</div>
        </section>
      </div>}
      <ContextComparison />
    </main>
  );
}
