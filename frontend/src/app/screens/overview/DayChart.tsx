import { rub } from "../../data";
import { budgetOf, daysOf, spentOf } from "../../store";
import type { State, Store } from "../../store";

const METRICS: [State["metric"], string][] = [["cost", "Расход"], ["leads", "Обращения"]];

export function DayChart({ s, store }: { s: State; store: Store }) {
  const days = daysOf(s);
  const today = days.length;
  const MONTH = s.live?.monthDays ?? 30;
  const monthOf = s.live?.monthOf ?? "ноября";
  const monthName = s.live?.monthName ?? "ноябрь";
  const ticks = [1, 10, 20, MONTH];
  const columns = { gridTemplateColumns: `repeat(${MONTH}, minmax(0, 1fr))` };
  const budget = budgetOf(s);
  const perDay = budget > 0 ? Math.max(0, (budget - spentOf(s)) / Math.max(1, MONTH - today)) : 0;
  const cost = s.metric === "cost";
  const maxDayCost = Math.max(...days.map((d) => d[0]), 0);
  const top = cost ? (budget > 0 || maxDayCost > 0 ? Math.max(maxDayCost, perDay, 1000) : 0) : Math.max(4, ...days.map((d) => d[1]));
  const sel = Math.max(0, Math.min(s.selDay ?? (today > 0 ? today - 1 : 0), MONTH - 1));
  const [selCost, selLeads] = days[sel] ?? [0, 0];

  return (
    <section aria-labelledby="ov-chart-title" className="ah-panel ov-chart-panel">
      <div className="ov-chart-head">
        <h2 id="ov-chart-title" className="ah-panel-title">{monthName[0]!.toUpperCase() + monthName.slice(1)} по дням</h2>
        <div className="ah-chips">
          {METRICS.map(([id, label]) => (
            <button key={id} type="button" className="ah-chip" aria-pressed={s.metric === id} onClick={() => store.set({ metric: id })}>{label}</button>
          ))}
        </div>
      </div>
      {today === 0 ? (
        <p className="ov-chart-empty" role="status">Показы появятся здесь после запуска рекламы.</p>
      ) : <div className="ov-chart" style={columns}>
        <span className="ov-chart-top">{cost ? rub(top) : String(top)}</span>
        {cost && !s.paused && top > 0 && perDay > 0 && <div className="ov-chart-limit" aria-hidden="true" style={{ bottom: `${Math.round((perDay * 100) / top)}%` }} />}
        {Array.from({ length: MONTH }, (_, i) => {
          const day = days[i];
          const value = day ? day[cost ? 0 : 1] : cost ? perDay : 0;
          const height = top > 0 ? Math.max(day || cost ? 2 : 0, Math.round((value * 100) / top)) : 0;
          const label = day ? `${i + 1} ${monthOf}: ${rub(day[0])}, обращений ${day[1]}` : `${i + 1} ${monthOf}: можно до ${rub(perDay)}`;
          const kind = !day ? "ov-bar-plan" : i === sel ? "ov-bar-picked" : "ov-bar-past";
          return (
            <button key={i} type="button" className="ov-bar" aria-label={label} title={label} aria-pressed={day ? i === sel : undefined} disabled={!day} onClick={() => store.set({ selDay: i })}>
              {height > 0 && <span className={kind} style={{ height: `${height}%` }} />}
            </button>
          );
        })}
      </div>}
      {today > 0 && <div className="ov-chart-ticks" aria-hidden="true" style={columns}>
        {ticks.map((d) => <span key={d} style={{ gridColumn: d }}>{d}</span>)}
      </div>}
      {today > 0 && <p className="ov-chart-note" aria-live="polite">
        {budget <= 0
          ? (!s.live?.hasCampaigns && s.live?.planBudget
            ? `План ${rub(s.live.planBudget)}, реклама не запущена.`
            : "Бюджет на месяц ещё не задан. Укажите сумму в настройках, чтобы автопилот рассчитал дневной лимит показов.")
          : today === 0
            ? `Показов в этом месяце ещё не было. Лимит до ${rub(perDay)} в день, чтобы уложиться в бюджет.`
            : `${sel + 1} ${monthOf}: ${rub(selCost)}, обращений ${selLeads}. Пунктир и контур: дальше можно до ${rub(perDay)} в день, чтобы уложиться в бюджет.`}
      </p>}
    </section>
  );
}
