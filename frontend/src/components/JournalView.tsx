import type { JournalEntry } from "../App";

interface Props {
  journal: JournalEntry[];
}

export default function JournalView({ journal }: Props) {
  return (
    <section className="view active">
      <div className="journal-panel">
        <h2>Журнал</h2>
        <p className="hint">
          Безопасные метаданные запросов. Исходные тексты, восстановленные
          значения и ключи не сохраняются.
        </p>
        <div className="journal-list">
          {journal.length === 0 && (
            <div className="empty-state">
              <p>Журнал пуст. Выполните обработку текста, чтобы увидеть безопасные записи.</p>
            </div>
          )}
          {journal.map((e, i) => (
            <div key={i} className="journal-entry">
              <span className="j-field"><span className="j-label">Время:</span> {e.time}</span>
              <span className="j-field"><span className="j-label">Запрос:</span> {e.request_id}</span>
              <span className="j-field"><span className="j-label">Приложение:</span> {e.consumer}</span>
              <span className="j-field"><span className="j-label">Операция:</span> {e.operation}</span>
              <span className="j-field">
                <span className="j-label">ПД:</span>{" "}
                {Object.entries(e.detected).map(([k, v]) => `${k}:${v}`).join(", ") || "нет"}
              </span>
              <span className="j-field"><span className="j-label">Статус:</span> {e.outcome}</span>
              <span className="j-field"><span className="j-label">Политика:</span> {e.policy}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}