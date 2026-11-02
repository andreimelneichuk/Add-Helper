import { DAYS, rub } from "../../data";
import { budgetOf, spentOf } from "../../store";
import type { State, Store } from "../../store";

const MONTH = 30;
const TICKS = [1, 10, 20, 30];
const METRICS: [State["metric"], string][] = [["cost", "Расход"], ["leads", "Обращения"]];

export function DayChart({ s, store }: { s: State; store: Store }) {
  const days = DAYS[s.situation];
  const today = days.length;
  const perDay = Math.max(0, (budgetOf(s) - spentOf(s)) / (MONTH - today));
  const cost = s.metric === "cost";
  const top = cost ? Math.max(...days.map((d) => d[0]), perDay, 1000) : Math.max(4, ...days.map((d) => d[1]));
  const sel = s.selDay ?? today - 1;
  const [selCost, selLeads] = days[sel] ?? [0, 0];

  return (
    <section aria-labelledby="ov-chart-title" className="ah-panel ov-chart-panel">
      <div className="ov-chart-head">
        <h2 id="ov-chart-title" className="ah-panel-title">Ноябрь по дням</h2>
        <div className="ah-chips">
          {METRICS.map(([id, label]) => (
            <button key={id} type="button" className="ah-chip" aria-pressed={s.metric === id} onClick={() => store.set({ metric: id })}>{label}</button>
          ))}
        </div>
      </div>
      <div className="ov-chart">
        <span className="ov-chart-top">{cost ? rub(top) : String(top)}</span>
        {cost && !s.paused && <div className="ov-chart-limit" aria-hidden="true" style={{ bottom: `${Math.round((perDay * 100) / top)}%` }} />}
        {Array.from({ length: MONTH }, (_, i) => {
          const day = days[i];
          const value = day ? day[cost ? 0 : 1] : cost ? perDay : 0;
          const height = Math.max(day || cost ? 2 : 0, Math.round((value * 100) / top));
          const label = day ? `${i + 1} ноября: ${rub(day[0])}, обращений ${day[1]}` : `${i + 1} ноября: можно до ${rub(perDay)}`;
          const kind = !day ? "ov-bar-plan" : i === sel ? "ov-bar-picked" : "ov-bar-past";
          return (
            <button key={i} type="button" className="ov-bar" aria-label={label} title={label} aria-pressed={day ? i === sel : undefined} disabled={!day} onClick={() => store.set({ selDay: i })}>
              {height > 0 && <span className={kind} style={{ height: `${height}%` }} />}
            </button>
          );
        })}
      </div>
      <div className="ov-chart-ticks" aria-hidden="true">
        {TICKS.map((d) => <span key={d} style={{ gridColumn: d }}>{d}</span>)}
      </div>
      <p className="ov-chart-note" aria-live="polite">{`${sel + 1} ноября: ${rub(selCost)}, обращений ${selLeads}. Пунктир и контур: дальше можно до ${rub(perDay)} в день, чтобы уложиться в бюджет.`}</p>
    </section>
  );
}
