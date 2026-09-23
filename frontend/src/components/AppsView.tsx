import { useState } from "react";
import { api } from "../lib/api";
import type { ConfigResponse } from "../lib/types";

interface Props {
  config: ConfigResponse | null;
  showToast: (msg: string, error?: boolean) => void;
  onConfigChanged: () => void;
}

interface FormState {
  enabled: boolean;
  allow_unmask: boolean;
  allow_llm_egress: boolean;
  default_action: string;
  detect_types: string;
}

export default function AppsView({ config, showToast, onConfigChanged }: Props) {
  const [adminKey, setAdminKey] = useState("");
  const [saving, setSaving] = useState<string | null>(null);

  if (!config) {
    return (
      <section className="view active">
        <div className="apps-panel">
          <h2>Приложения</h2>
          <p className="hint">Загрузка конфигурации…</p>
        </div>
      </section>
    );
  }

  const save = async (name: string, form: FormState) => {
    if (!adminKey) {
      showToast("Введите admin-ключ", true);
      return;
    }
    setSaving(name);
    try {
      const updates: Record<string, unknown> = {
        enabled: form.enabled,
        allow_unmask: form.allow_unmask,
        allow_llm_egress: form.allow_llm_egress,
        default_action: form.default_action,
        detect_types: form.detect_types,
      };
      const resp = await api.updateConfig(name, updates, adminKey);
      void resp;
      showToast("Конфигурация сохранена: " + name);
      onConfigChanged();
    } catch (e) {
      showToast("Ошибка: " + (e as Error).message, true);
    } finally {
      setSaving(null);
    }
  };

  return (
    <section className="view active">
      <div className="apps-panel">
        <h2>Приложения</h2>
        <p className="hint">
          Настройки потребителей. Изменения применяются серверно и атомарно;
          невалидные изменения отклоняются и не ломают активную конфигурацию.
        </p>
        <div className="auth-panel">
          <label>
            Admin-ключ (PII_CONFIG_UPDATE_KEY)
            <input
              type="password"
              value={adminKey}
              onChange={(e) => setAdminKey(e.target.value)}
              placeholder="Введите admin-ключ"
              autoComplete="off"
            />
          </label>
        </div>
        <div className="apps-list">
          {config.consumers.map((name) => {
            const info = config.consumer_info[name];
            if (!info) return null;
            return (
              <ConsumerCard
                key={name}
                name={name}
                info={info}
                saving={saving === name}
                onSave={(form) => save(name, form)}
              />
            );
          })}
        </div>
      </div>
    </section>
  );
}

function ConsumerCard({
  name, info, saving, onSave,
}: {
  name: string;
  info: { enabled: boolean; allow_unmask: boolean; allow_llm_egress: boolean; mask_action: string; detect_types: string };
  saving: boolean;
  onSave: (form: FormState) => void;
}) {
  const [form, setForm] = useState<FormState>({
    enabled: info.enabled,
    allow_unmask: info.allow_unmask,
    allow_llm_egress: info.allow_llm_egress,
    default_action: info.mask_action,
    detect_types: info.detect_types,
  });

  return (
    <div className="app-card">
      <h3>{name}</h3>
      <div className="app-form">
        <label className="app-check">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          Включено
        </label>
        <label className="app-check">
          <input
            type="checkbox"
            checked={form.allow_unmask}
            onChange={(e) => setForm({ ...form, allow_unmask: e.target.checked })}
          />
          Демаскирование
        </label>
        <label className="app-check">
          <input
            type="checkbox"
            checked={form.allow_llm_egress}
            onChange={(e) => setForm({ ...form, allow_llm_egress: e.target.checked })}
          />
          Egress (вызов модели)
        </label>
        <label className="app-check">
          Маска:
          <select
            value={form.default_action}
            onChange={(e) => setForm({ ...form, default_action: e.target.value })}
          >
            <option value="tokenize_full">tokenize_full</option>
            <option value="opaque_token_full">opaque_token_full</option>
          </select>
        </label>
        <label className="app-check">
          Типы:
          <input
            type="text"
            value={form.detect_types}
            onChange={(e) => setForm({ ...form, detect_types: e.target.value })}
          />
        </label>
        <button className="primary-btn" onClick={() => onSave(form)} disabled={saving}>
          {saving ? "Сохранение…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}