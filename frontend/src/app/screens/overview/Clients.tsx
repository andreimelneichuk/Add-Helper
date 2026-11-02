import { useState } from "react";
import type { FormEvent } from "react";
import { week } from "../../store";
import type { State, Store } from "../../store";

const clientsWord = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? "клиента" : "клиентов");

// Без этого ответа нет цены клиента, поэтому вопрос стоит сразу под выводом, а не внутри блоков ниже.
export function Clients({ s, store }: { s: State; store: Store }) {
  const [error, setError] = useState("");
  // Ответ о клиентах сервис пока не хранит: в живом режиме вопрос не задаём.
  if (s.live) return null;
  if (s.situation === "problem" || s.situation === "first" || s.situation === "access") return null;
  const leads = week(s).leads;
  if (s.clients) return <div className="ah-panel ov-clients ah-muted">{`Записал ${s.clients} ${clientsWord(s.clients)}. Цена клиента теперь считается.`}</div>;
  const save = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(s.clientsInput);
    if (n > leads) return setError(`Обращений было ${leads}, поэтому клиентов не больше ${leads}.`);
    setError("");
    if (n > 0) store.run({ action: "clients", value: n });
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
        <button type="submit" className="ah-btn ah-btn-primary">Записать</button>
      </div>
    </form>
  );
}
