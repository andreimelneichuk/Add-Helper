import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../../../api";
import { rub } from "../../data";
import { dayAt, dayNumber, liveApi } from "../../live";
import type { LiveStore } from "../../live";
import { week } from "../../store";
import type { State, Store } from "../../store";

const clientsWord = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? "клиента" : "клиентов");

// Без этого ответа нет цены клиента, поэтому вопрос стоит сразу под выводом, а не внутри блоков ниже.
export function Clients({ s, store }: { s: State; store: Store }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const api = liveApi(store);
  if (s.live && (s.paused || !s.live.hasCampaigns)) return null;
  if (!s.live && (s.situation === "problem" || s.situation === "first" || s.situation === "access")) return null;
  const leads = week(s).leads;
  if (leads <= 0) return null;
  if (s.clients !== null) {
    const detail = s.clients > 0 ? "Цена клиента теперь считается." : "Клиентов пока нет.";
    return <div className="ah-panel ov-clients ah-muted">{`Записал ${s.clients} ${clientsWord(s.clients)}. ${detail}`}</div>;
  }
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (s.clientsInput.trim() === "") return;
    const n = Number(s.clientsInput);
    if (n > leads) return setError(`Обращений было ${leads}, поэтому клиентов не больше ${leads}.`);
    setError("");
    if (n < 0) return;
    if (!api || !s.live) return store.run({ action: "clients", value: n });
    // Ответ — данные для цены клиента, площадку он не меняет, поэтому идёт прямо в API.
    const today = s.live.today;
    setBusy(true);
    api.answerClients({ since: dayAt(dayNumber(today) - 6), until: today }, n)
      .then(async () => {
        await (store as LiveStore).refresh();
        const costPart = n > 0 ? `Клиент обошёлся в ${rub(week(store.state).cost / n)}.` : "Клиентов пока нет.";
        store.set({ notice: `Записал ${n} из ${leads}. ${costPart} Исправить можно, ответив ещё раз.` });
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Сервис не ответил. Ничего не записал, попробуйте позже."))
      .finally(() => setBusy(false));
  };
  return (
    <form className="ah-panel ov-clients" onSubmit={save}>
      <div className="ov-clients-text">
        <label htmlFor="ov-clients" className="ah-panel-title">Сколько из {leads} обращений стали клиентами?</label>
        <p className="ah-muted">Загляните в журнал записи за эти 7 дней. Примерное число тоже подойдёт.</p>
        {error && <p role="alert" className="ah-error">{error}</p>}
      </div>
      <div className="ov-clients-row">
        <input id="ov-clients" className="ah-input" inputMode="numeric" autoComplete="off" value={s.clientsInput} onChange={(e) => { setError(""); store.set({ clientsInput: e.target.value.replace(/\D/g, "") }); }} />
        <button type="submit" className="ah-btn ah-btn-primary" disabled={busy}>Записать</button>
      </div>
    </form>
  );
}
