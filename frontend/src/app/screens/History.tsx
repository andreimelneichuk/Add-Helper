import { useEffect, useId, useRef } from "react";
import type { HistoryEntry } from "../data";
import { useStoreState } from "../store";
import type { State, Store } from "../store";
import "./History.css";

const FILTERS: [State["filter"], string][] = [["all", "Все"], ["money", "Деньги"], ["ads", "Объявления"], ["tests", "Проверки"], ["yours", "Ваши"]];

function Entry({ store, entry }: { store: Store; entry: HistoryEntry }) {
  const s = useStoreState(store);
  const undone = !!s.undone[entry.id];
  const can = entry.canUndo && !undone;
  const asked = !!s.asked[entry.id] && can;
  const state = undone ? "Отменено вами" : entry.state ?? "";
  const titleId = useId();
  const noteId = useId();
  const askRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const stateRef = useRef<HTMLSpanElement>(null);
  const wasAsked = useRef(asked);

  // Нажатая кнопка исчезает из строки: фокус переходит на то, что появилось вместо неё.
  useEffect(() => {
    if (wasAsked.current === asked) return;
    wasAsked.current = asked;
    if (asked) keepRef.current?.focus();
    else if (can) askRef.current?.focus();
    else stateRef.current?.focus();
  }, [asked, can]);

  const toggle = () => store.set({ asked: { ...store.state.asked, [entry.id]: !store.state.asked[entry.id] } });

  return (
    <article className="ah-hist-row" aria-labelledby={titleId}>
      <div className="ah-hist-when">{entry.when}</div>
      <div className="ah-hist-body">
        <h2 id={titleId} className="ah-hist-title">{entry.title}</h2>
        <p className="ah-hist-text">{entry.why} {entry.next}</p>
        {asked && <p id={noteId} className="ah-hist-note">{entry.undoNote}</p>}
      </div>
      <div className="ah-hist-actions">
        {can && !asked && <button ref={askRef} type="button" className="ah-btn" aria-describedby={titleId} onClick={toggle}>Отменить</button>}
        {asked && (
          <>
            <button type="button" className="ah-btn ah-btn-primary" aria-describedby={noteId} onClick={() => store.run({ action: "undo", value: entry.id })}>Да, отменить</button>
            <button ref={keepRef} type="button" className="ah-btn" onClick={toggle}>Оставить</button>
          </>
        )}
        {!can && state && <span ref={stateRef} tabIndex={-1} className="ah-hist-state">{state}</span>}
      </div>
    </article>
  );
}

export function History({ store }: { store: Store }) {
  const s = useStoreState(store);
  const list = s.hist.filter((h) => s.filter === "all" || h.kind === s.filter);

  return (
    <section className="ah-panel ah-hist" aria-label="История">
      <div className="ah-hist-filters" role="group" aria-label="Какие записи показать">
        {FILTERS.map(([id, label]) => (
          <button key={id} type="button" className="ah-chip" aria-pressed={s.filter === id} onClick={() => store.set({ filter: id })}>{label}</button>
        ))}
      </div>
      {list.map((h) => <Entry key={h.id} store={store} entry={h} />)}
      {list.length === 0 && <p className="ah-hist-empty">Таких записей пока нет.</p>}
    </section>
  );
}
