import { useCallback, useEffect, useState } from "react";
import type { Api, AutopilotAction, AutopilotMode, AutopilotPatch, AutopilotStatus, AutopilotView } from "../../api";

// Слова владельца: «положение» вместо «режим», «разбор» вместо «цикл» (docs/01-product-spec.md).
export const AUTOPILOT_STATUS: Record<AutopilotStatus, string> = {
  ok: "Автопилот работает",
  no_goal: "Не задана желаемая цена обращения: без неё мне не с чем сравнивать",
  data_untrusted: "Данным в кабинете пока не верю: ничего не предлагаю, пока не разберёмся",
  stopped: "Автопилот остановлен: ничего не разбираю и не предлагаю",
  not_connected: "Кабинет Яндекса не подключён: разбирать нечего",
};

const POSITIONS: { mode: AutopilotMode; title: string; text: string }[] = [
  { mode: "watch", title: "Только следит", text: "Считает и показывает, что заметил. Ничего не предлагает и не меняет." },
  { mode: "confirm", title: "Предлагает", text: "Находит, что стоит поправить, и спрашивает вас. Без вашего «да» ничего не меняется." },
  { mode: "auto", title: "Действует сам", text: "Делает разрешённое сам, в рамках бюджета, и сообщает." },
];

const ACTIONS: { id: AutopilotAction; label: string }[] = [
  { id: "pause_ad", label: "Отключать объявления, которые не приводят обращений" },
  { id: "start_test", label: "Запускать проверку двух вариантов объявления" },
  { id: "increase_limit", label: "Поднимать лимит направлению, которое приводит обращения дёшево" },
];

const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

function when(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return date.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
}

function problem(error: unknown): string {
  return error instanceof Error ? error.message : "Не получилось, попробуйте ещё раз";
}

export function AutopilotSettings({ api }: { api: Api }) {
  const [view, setView] = useState<AutopilotView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await api.autopilot());
      setError("");
    } catch (e) {
      setError(problem(e));
    }
  }, [api]);
  useEffect(() => { void load(); }, [load]);

  const save = async (patch: AutopilotPatch) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setView(await api.saveAutopilot(patch));
    } catch (e) {
      setError(problem(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    setBusy(true);
    setError("");
    try {
      setView(await api.stopAutopilot());
      setConfirmStop(false);
    } catch (e) {
      setError(problem(e));
    } finally {
      setBusy(false);
    }
  };

  if (!view) return <p className="set-hint" aria-live="polite">{error || "Загружаю настройки автопилота…"}</p>;
  const stopped = view.status === "stopped";
  const toggle = (id: AutopilotAction) => {
    const has = view.allowed_actions.includes(id);
    const next = has ? view.allowed_actions.filter((a) => a !== id) : [...view.allowed_actions, id];
    if (next.length) void save({ allowed_actions: next });
  };
  return (
    <div className="ap">
      <p className="ap-status" aria-live="polite" data-status={view.status}>{AUTOPILOT_STATUS[view.status]}</p>
      {!stopped && view.next_run_at && <p className="set-hint">Следующий разбор: {when(view.next_run_at)} (по Москве)</p>}
      <div className="ap-cards" role="radiogroup" aria-label="Положение автопилота">
        {POSITIONS.map((p) => {
          const locked = p.mode === "auto" && !view.auto_allowed;
          const on = view.mode === p.mode;
          return (
            <button
              key={p.mode}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={busy || locked}
              className={`ap-card${on ? " ap-card-on" : ""}`}
              onClick={() => { if (!on) void save({ mode: p.mode }); }}
            >
              <strong>{p.title}</strong>
              <span>{p.text}</span>
              {locked && <em>Пока недоступно: ещё набираю данные, чтобы действовать сам безопасно</em>}
            </button>
          );
        })}
      </div>
      <fieldset className="ap-actions" disabled={busy}>
        <legend className="ah-label">Что можно делать</legend>
        <p className="set-hint">Когда автопилот действует сам, он делает только отмеченное. В положении «Предлагает» вы видите всё.</p>
        {ACTIONS.map((a) => (
          <label key={a.id} className="ap-check">
            <input type="checkbox" checked={view.allowed_actions.includes(a.id)} onChange={() => toggle(a.id)} />
            <span>{a.label}</span>
          </label>
        ))}
      </fieldset>
      <div className="ap-when">
        <label>
          <span className="ah-label">Как часто разбирать</span>
          <select className="ah-input" aria-label="Как часто разбирать" value={view.cadence} disabled={busy} onChange={(e) => void save({ cadence: e.target.value as "daily" | "weekly" })}>
            <option value="daily">Каждый день</option>
            <option value="weekly">Раз в неделю</option>
          </select>
        </label>
        <label>
          <span className="ah-label">В какой час (по Москве)</span>
          <select className="ah-input" aria-label="В какой час разбирать" value={view.run_hour} disabled={busy} onChange={(e) => void save({ run_hour: Number(e.target.value) })}>
            {HOURS.map((h) => <option key={h} value={h}>{h}:20</option>)}
          </select>
        </label>
      </div>
      <div className="ap-stop">
        {stopped ? (
          <button type="button" className="ah-btn" disabled={busy} onClick={() => void save({ resume: true })}>Снова включить автопилот</button>
        ) : confirmStop ? (
          <>
            <p>Остановить автопилот? Он перестанет разбирать рекламу и что-либо предлагать. Уже идущая реклама и её лимиты не изменятся.</p>
            <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={() => void stop()}>Да, остановить</button>
            <button type="button" className="ah-btn" disabled={busy} onClick={() => setConfirmStop(false)}>Не останавливать</button>
          </>
        ) : (
          <button type="button" className="ah-btn" disabled={busy} onClick={() => setConfirmStop(true)}>Остановить автопилот</button>
        )}
      </div>
      {error && <p className="ah-error" role="alert">{error}</p>}
      {view.history.length > 0 && (
        <ul className="ap-history" aria-label="Что менялось в настройках">
          {view.history.slice(0, 5).map((h, i) => <li key={`${h.at}-${i}`}><span>{when(h.at)}</span> {h.text}</li>)}
        </ul>
      )}
    </div>
  );
}
