import { rub } from "../../data";
import { budgetOf, daysOf, spentOf, week } from "../../store";
import type { State } from "../../store";

type Ink = "muted" | "good" | "bad";
type Kpi = { label: string; value: string; sub: string; ink: Ink; spark?: number[]; meter?: { pct: number; time: number } };

function sparkPath(values: number[]): string {
  const top = Math.max(...values, 1);
  return values.map((v, i) => `${i ? "L" : "M"}${((i * 100) / (values.length - 1)).toFixed(1)} ${(26 - (v * 24) / top).toFixed(1)}`).join(" ");
}

const MONTH_IN: Record<string, string> = { января: "январе", февраля: "феврале", марта: "марте", апреля: "апреле", мая: "мае", июня: "июне", июля: "июле", августа: "августе", сентября: "сентябре", октября: "октябре", ноября: "ноябре", декабря: "декабре" };

function kpisOf(s: State): Kpi[] {
  const days = daysOf(s);
  const w = week(s);
  const budget = budgetOf(s);
  const spent = spentOf(s);
  const cpa = w.cost / Math.max(1, w.leads);
  const prevCpa = w.prevCost / Math.max(1, w.prevLeads);
  const last14 = s.live?.trail ?? days.slice(-14);
  const hasCampaigns = s.live ? s.live.hasCampaigns : true;
  const planBudget = s.live?.planBudget ?? null;
  const budgetSub = budget > 0
    ? `из ${rub(budget)}, осталось ${rub(Math.max(0, budget - spent))}`
    : (!hasCampaigns && planBudget)
      ? `План ${rub(planBudget)}, реклама не запущена`
      : "Бюджет на месяц ещё не задан";

  const money: Kpi = {
    label: `Потрачено в ${s.live ? MONTH_IN[s.live.monthOf] ?? "этом месяце" : "ноябре"}`,
    value: s.live && s.live.spent === null ? "нет данных" : rub(spent),
    sub: budgetSub,
    ink: "muted",
    meter: { pct: budget > 0 ? Math.min(100, Math.round((spent * 100) / budget)) : 0, time: Math.round((days.length * 100) / (s.live?.monthDays ?? 30)) },
  };
  const clients: Kpi = {
    label: "Клиенты, 7 дней",
    value: s.clients !== null ? String(s.clients) : "нет ответа",
    sub: s.clients !== null ? (s.clients > 0 ? `цена клиента ${rub(w.cost / s.clients)}` : "клиентов пока нет") : "спрашиваю раз в неделю",
    ink: "muted",
  };
  if (s.live) {
    const price = (cost: number, leads: number) => (leads > 0 ? rub(cost / leads) : "—");
    const base: Kpi[] = [
      money,
      { label: "Обращения, 7 дней", value: String(w.leads), sub: `было ${w.prevLeads}`, ink: w.leads >= w.prevLeads ? "good" : "bad", spark: last14.map((d) => d[1]) },
      { label: "Цена обращения", value: price(w.cost, w.leads), sub: `было ${price(w.prevCost, w.prevLeads)}`, ink: "muted", spark: last14.map((d) => (d[1] > 0 ? d[0] / d[1] : 0)) },
    ];
    return s.clients !== null ? [...base, clients] : base;
  }
  // Первые дни: данных мало, поэтому сравнений и выводов о цене нет.
  if (s.situation === "first") {
    return [
      money,
      { label: `Обращения, ${days.length} дня`, value: String(w.leads), sub: "мало для выводов", ink: "muted" },
      { label: "Цена обращения", value: rub(cpa), sub: "мало для выводов", ink: "muted" },
      clients,
    ];
  }
  return [
    money,
    { label: "Обращения, 7 дней", value: String(w.leads), sub: `было ${w.prevLeads}`, ink: w.leads >= w.prevLeads ? "good" : "bad", spark: last14.map((d) => d[1]) },
    { label: "Цена обращения", value: rub(cpa), sub: `было ${rub(prevCpa)}, цель ${rub(Number(s.f.goal))}`, ink: cpa <= Number(s.f.goal) ? "good" : cpa <= prevCpa ? "muted" : "bad", spark: last14.map((d) => d[0] / Math.max(1, d[1])) },
    clients,
  ];
}

export function Kpis({ s }: { s: State }) {
  const kpis = kpisOf(s);
  return (
    <div className={kpis.length === 3 ? "ov-kpis ov-kpis-three" : "ov-kpis"}>
      {kpis.map((k) => (
        <div key={k.label} className="ah-panel ov-kpi">
          <div className="ov-kpi-label">{k.label}</div>
          <div className="ov-kpi-value">{k.value}</div>
          <div className={`ov-kpi-sub ov-ink-${k.ink}`}>{k.sub}</div>
          {k.spark && (
            <svg className="ov-spark" width="100%" height="28" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">
              <path d={sparkPath(k.spark)} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
          {k.meter && (
            <div className="ov-meter" aria-hidden="true">
              <div className="ov-meter-fill" style={{ width: `${k.meter.pct}%` }} />
              <div className="ov-meter-now" style={{ left: `${k.meter.time}%` }} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
