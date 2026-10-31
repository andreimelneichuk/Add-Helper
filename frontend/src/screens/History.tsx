import { useState } from "react";
import { HISTORY, applyRollback, type HistoryEntry } from "../testData";

export function History({ onHome }: { onHome: () => void }) {
  const [entries, setEntries] = useState<HistoryEntry[]>(HISTORY);
  const [asking, setAsking] = useState<number | null>(null);

  return (
    <main className="screen">
      <h1>Что делала система</h1>
      {entries.map((entry) => (
        <article className={entry.reverted ? "card rejected" : "card"} key={entry.id}>
          <h3>{entry.title}</h3>
          <p>{entry.why}</p>
          <p className="muted">{entry.next} {entry.when[0]?.toUpperCase()}{entry.when.slice(1)}.</p>
          {entry.reverted && <p className="muted">Отменено</p>}
          {!entry.reverted && !entry.isRollback && entry.rollback === "impossible" && <p className="muted">{entry.consequence}</p>}
          {!entry.reverted && !entry.isRollback && entry.rollback !== "impossible" && asking !== entry.id && (
            <button type="button" className="link" onClick={() => setAsking(entry.id)}>Отменить</button>
          )}
          {asking === entry.id && (
            <div className="confirm" role="group" aria-label="Подтверждение отмены">
              <p>{entry.consequence}</p>
              <button
                type="button"
                className="primary"
                onClick={() => { setEntries(applyRollback(entries, entry.id)); setAsking(null); }}
              >
                Да, отменить
              </button>
              <button type="button" className="link" onClick={() => setAsking(null)}>Оставить как есть</button>
            </div>
          )}
        </article>
      ))}
      <button type="button" className="link" onClick={onHome}>На главную</button>
    </main>
  );
}
