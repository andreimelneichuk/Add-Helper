import { useEffect, useRef, useState } from "react";
import type { Range } from "../api";
import { length, MAX_DAYS, PRESETS, presetOf, rangeLabel } from "./period";

// Выбор периода отчёта: готовые варианты и свои даты. Конец не позже сегодняшнего дня.
export function PeriodPicker({ range, today, onChange }: { range: Range; today: string; onChange: (r: Range) => void }) {
  const [open, setOpen] = useState(false);
  const [since, setSince] = useState(range.since);
  const [until, setUntil] = useState(range.until);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSince(range.since);
    setUntil(range.until);
  }, [range.since, range.until]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const active = presetOf(range, today);
  const choose = (next: Range) => {
    onChange(next);
    setOpen(false);
  };
  const problem =
    !since || !until
      ? "Укажите обе даты"
      : since > until
        ? "Начало позже конца"
        : until > today
          ? "Статистики за будущие дни ещё нет"
          : length({ since, until }) > MAX_DAYS
            ? "Не больше года"
            : null;

  return (
    <div className="cab-picker" ref={box}>
      <div className="cab-quick" role="group" aria-label="Быстрый выбор периода">
        {PRESETS.slice(2, 5).map((p) => (
          <button
            key={p.id}
            type="button"
            className={active?.id === p.id ? "cab-chip cab-chip-on" : "cab-chip"}
            aria-pressed={active?.id === p.id}
            onClick={() => choose(p.range(today))}
          >
            {p.label}
          </button>
        ))}
      </div>
      <button type="button" className="cab-picker-button" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 2v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2zm-2 8h14v10H5z" /></svg>
        <span>{active ? `${active.label}: ` : ""}{rangeLabel(range)}</span>
      </button>
      {open && (
        <div className="cab-popover" role="dialog" aria-label="Период отчёта">
          <ul className="cab-presets">
            {PRESETS.map((p) => (
              <li key={p.id}>
                <button type="button" className={active?.id === p.id ? "cab-preset cab-preset-on" : "cab-preset"} onClick={() => choose(p.range(today))}>
                  {p.label}
                  <span className="cab-muted">{rangeLabel(p.range(today)).split(" · ")[0]}</span>
                </button>
              </li>
            ))}
          </ul>
          <form
            className="cab-custom"
            onSubmit={(e) => {
              e.preventDefault();
              if (!problem) choose({ since, until });
            }}
          >
            <strong>Свои даты</strong>
            <label>
              <span className="cab-muted">С</span>
              <input type="date" value={since} max={today} onChange={(e) => setSince(e.target.value)} />
            </label>
            <label>
              <span className="cab-muted">По</span>
              <input type="date" value={until} max={today} onChange={(e) => setUntil(e.target.value)} />
            </label>
            {problem ? <p className="cab-bad cab-small">{problem}</p> : <p className="cab-muted cab-small">{rangeLabel({ since, until })}</p>}
            <button type="submit" className="cab-btn cab-btn-primary" disabled={problem !== null}>Показать</button>
          </form>
        </div>
      )}
    </div>
  );
}
