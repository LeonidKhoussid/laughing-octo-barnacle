import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import type { ConfigResponse, ConsumerInfo } from "./lib/types";
import Workspace from "./components/Workspace";
import AppsView from "./components/AppsView";
import JournalView from "./components/JournalView";
import JudgeDemo from "./components/JudgeDemo";

export type View = "demo" | "workspace" | "apps" | "journal";

export interface JournalEntry {
  time: string;
  request_id: string;
  consumer: string;
  operation: string;
  detected: Record<string, number>;
  duration_ms: number | null;
  outcome: string;
  policy: string;
}

const VIEW_TITLES: Record<View, string> = {
  demo: "Проверка защиты данных",
  workspace: "Расширенная проверка",
  apps: "Приложения",
  journal: "Журнал",
};

export default function App() {
  const [view, setView] = useState<View>("demo");
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [consumer, setConsumer] = useState<string>("support_demo");
  const [apiKey, setApiKey] = useState<string>("");
  const [journal, setJournal] = useState<JournalEntry[]>([]);
  const [toast, setToast] = useState<{ msg: string; error?: boolean } | null>(null);
  const toastTimer = useRef<number | null>(null);

  const loadConfig = useCallback(async () => {
    try {
      const cfg = await api.getConfig();
      setConfig(cfg);
      if (!cfg.consumers.includes(consumer)) {
        setConsumer(cfg.consumers[0] || "support_demo");
      }
    } catch (e) {
      showToast("Не удалось загрузить конфигурацию: " + (e as Error).message, true);
    }
  }, [consumer]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const showToast = useCallback((msg: string, error?: boolean) => {
    setToast({ msg, error });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3200);
  }, []);

  const addJournal = useCallback((entry: JournalEntry) => {
    setJournal((prev) => [entry, ...prev].slice(0, 50));
  }, []);

  const handleConsumerChange = useCallback((name: string) => {
    setConsumer(name);
    setApiKey("");
  }, []);

  const consumerInfo: ConsumerInfo | undefined =
    config?.consumer_info?.[consumer];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">AlfaGen</div>
        <nav className="nav" aria-label="Основная навигация">
          <button
            className={"nav-item" + (view === "demo" ? " active" : "")}
            onClick={() => setView("demo")}
            aria-current={view === "demo" ? "page" : undefined}
          >Демонстрация</button>
          <details className="advanced-nav" open={view !== "demo" || undefined}>
            <summary>Инструменты</summary>
          {(["workspace", "apps", "journal"] as View[]).map((v) => (
            <button
              key={v}
              className={"nav-item" + (view === v ? " active" : "")}
              onClick={() => setView(v)}
              aria-current={view === v ? "page" : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                {v === "workspace" ? "▤" : v === "apps" ? "◈" : "☰"}
              </span>
              <span>{VIEW_TITLES[v]}</span>
            </button>
          ))}
          </details>
        </nav>
        {view !== "demo" && <div className="sidebar-footer">
          <span className="badge badge-muted">
            Политика: {config?.policy_version || "—"}
          </span>
        </div>}
      </aside>

      <div className="main">
        <header className="header">
          <h1>{VIEW_TITLES[view]}</h1>
          {view !== "demo" && <div className="header-controls">
            <label className="app-label" htmlFor="consumer">Приложение</label>
            <div className="app-select-wrap">
              <select
                id="consumer"
                value={consumer}
                onChange={(e) => handleConsumerChange(e.target.value)}
                aria-label="Выбор приложения"
              >
                {(config?.consumers || []).map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
              <button
                className="icon-btn"
                onClick={() => setView("apps")}
                aria-label="Настройки приложения"
                title="Настройки приложения"
              >
                ⚙
              </button>
            </div>
          </div>}
        </header>

        {view === "demo" && <JudgeDemo addJournal={addJournal} />}
        {view === "workspace" && (
          <Workspace
            consumer={consumer}
            consumerInfo={consumerInfo}
            apiKey={apiKey}
            setApiKey={setApiKey}
            addJournal={addJournal}
            showToast={showToast}
          />
        )}
        {view === "apps" && (
          <AppsView
            config={config}
            showToast={showToast}
            onConfigChanged={loadConfig}
          />
        )}
        {view === "journal" && <JournalView journal={journal} />}
      </div>

      {toast && (
        <div className={"toast show" + (toast.error ? " error" : "")} role="status">
          {toast.msg}
        </div>
      )}
    </div>
  );
}
