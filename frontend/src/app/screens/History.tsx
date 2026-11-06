import { memo, useEffect, useId, useRef } from "react";
import type { UndoPreview } from "../../api";
import type { HistoryEntry } from "../data";
import { useStoreState } from "../store";
import type { State, Store } from "../store";
import "./History.css";

const FILTERS: [State["filter"], string][] = [["all", "Все"], ["money", "Деньги"], ["ads", "Объявления"], ["tests", "Проверки"], ["yours", "Ваши"]];

type EntryProps = {
  entry: HistoryEntry;
  undone: boolean;
  undoing: boolean;
  asked: boolean;
  info?: UndoPreview;
  onAskUndo: (id: string) => void;
  onRunUndo: (id: string, whole?: boolean) => void;
};

const Entry = memo(function Entry({ entry, undone, undoing, asked, info, onAskUndo, onRunUndo }: EntryProps) {
  const can = entry.canUndo && !undone && !undoing;
  const isAsked = asked && can;
  // Сервис проверил заранее: если отменить нельзя, говорим почему и не предлагаем кнопку.
  const possible = info?.possible ?? true;
  const many = info && info.possible && info.directions > 1 ? info.directions : 0;
  const state = undoing ? "Отменяю…" : undone ? "Отменено вами" : entry.state ?? "";
  const titleId = useId();
  const noteId = useId();
  const askRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const stateRef = useRef<HTMLSpanElement>(null);
  const wasAsked = useRef(isAsked);

  // Нажатая кнопка исчезает из строки: фокус переходит на то, что появилось вместо неё.
  useEffect(() => {
    if (wasAsked.current === isAsked) return;
    wasAsked.current = isAsked;
    if (isAsked) keepRef.current?.focus();
    else if (can) askRef.current?.focus();
    else stateRef.current?.focus();
  }, [isAsked, can]);

  const toggle = () => onAskUndo(entry.id);

  return (
    <article className="ah-hist-row" aria-labelledby={titleId}>
      <div className="ah-hist-when">{entry.when}</div>
      <div className="ah-hist-body">
        <h2 id={titleId} className="ah-hist-title">{entry.title}</h2>
        <p className="ah-hist-text">{entry.why} {entry.next}</p>
        {isAsked && <p id={noteId} className="ah-hist-note" role="status">{info?.text ?? entry.undoNote}</p>}
      </div>
      <div className="ah-hist-actions">
        {can && !isAsked && <button ref={askRef} type="button" className="ah-btn" aria-describedby={titleId} onClick={toggle}>Отменить</button>}
        {isAsked && (
          <>
            {possible && many > 0 && (
              <button type="button" className="ah-btn ah-btn-primary" aria-describedby={noteId} onClick={() => onRunUndo(entry.id, true)}>Отменить для всех {many} направлений</button>
            )}
            {possible && (
              <button type="button" className={many ? "ah-btn" : "ah-btn ah-btn-primary"} aria-describedby={noteId} onClick={() => onRunUndo(entry.id)}>{many ? "Только это направление" : "Да, отменить"}</button>
            )}
            <button ref={keepRef} type="button" className="ah-btn" onClick={toggle}>{possible ? "Оставить" : "Понятно"}</button>
          </>
        )}
        {!can && state && <span ref={stateRef} tabIndex={-1} className="ah-hist-state">{state}</span>}
      </div>
    </article>
  );
});

export function History({ store }: { store: Store }) {
  const s = useStoreState(store);
  const list = s.hist.filter((h) => s.filter === "all" || h.kind === s.filter);

  const handleAskUndo = (id: string) => store.askUndo(id);
  const handleRunUndo = (id: string, whole?: boolean) => store.run({ action: "undo", value: id, whole });

  return (
    <section className="ah-panel ah-hist" aria-label="История">
      <div className="ah-hist-filters" role="group" aria-label="Какие записи показать">
        {FILTERS.map(([id, label]) => (
          <button key={id} type="button" className="ah-chip" aria-pressed={s.filter === id} onClick={() => store.set({ filter: id })}>{label}</button>
        ))}
      </div>
      {list.map((h) => (
        <Entry
          key={h.id}
          entry={h}
          undone={!!s.undone[h.id]}
          undoing={!!s.undoing?.[h.id]}
          asked={!!s.asked[h.id]}
          info={s.undoInfo[h.id]}
          onAskUndo={handleAskUndo}
          onRunUndo={handleRunUndo}
        />
      ))}
      {list.length === 0 && <p className="ah-hist-empty">Таких записей пока нет.</p>}
    </section>
  );
}
