import { useState } from "react";
import type { Overview } from "../api";
import { count, percent, rub, rubExact, shortDate, weekday } from "../format";
import { PeriodNote } from "./Cabinet";
import { DailyChart, METRIC_LABEL, type Metric, sumTotals } from "./Chart";

const METRICS: Metric[] = ["cost", "clicks", "impressions", "conversions"];

export function Stats({ overview }: { overview: Overview }) {
  const [metric, setMetric] = useState<Metric>("cost");
  const totals = sumTotals(overview.days);
  const rows = [...overview.days].reverse();
  return (
    <>
      <div className="cab-head">
        <h1>Статистика</h1>
        <PeriodNote overview={overview} />
      </div>
      <section className="cab-panel">
        <div className="cab-tabs" role="tablist" aria-label="Показатель на графике">
          {METRICS.map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={metric === m}
              className={metric === m ? "cab-tab cab-tab-active" : "cab-tab"}
              onClick={() => setMetric(m)}
            >
              {METRIC_LABEL[m]}
              <span className="cab-tab-value">{m === "cost" ? rub(totals.cost) : count(totals[m])}</span>
            </button>
          ))}
        </div>
        <DailyChart days={overview.days} metric={metric} line={metric === "conversions" ? "clicks" : "conversions"} />
      </section>
      <section className="cab-panel">
        <h2>По дням</h2>
        <div className="cab-table-wrap">
          <table className="cab-table">
            <thead>
              <tr>
                <th className="cab-left">День</th>
                <th>Показы</th>
                <th>Клики</th>
                <th>CTR</th>
                <th>Цена клика</th>
                <th>Расход</th>
                <th>Обращения</th>
                <th>Цена обращения</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ day, totals: t }) => (
                <tr key={day}>
                  <td className="cab-left">{shortDate(day)} <span className="cab-muted">{weekday(day)}</span></td>
                  <td>{count(t.impressions)}</td>
                  <td>{count(t.clicks)}</td>
                  <td>{percent(t.impressions ? Math.floor((t.clicks * 10_000) / t.impressions) : null)}</td>
                  <td>{rubExact(t.clicks ? Math.floor(t.cost / t.clicks) : null)}</td>
                  <td>{rub(t.cost)}</td>
                  <td>{count(t.conversions)}</td>
                  <td>{t.conversions ? rub(Math.floor(t.cost / t.conversions)) : <span className="cab-muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th className="cab-left">Итого</th>
                <th>{count(totals.impressions)}</th>
                <th>{count(totals.clicks)}</th>
                <th>{percent(totals.impressions ? Math.floor((totals.clicks * 10_000) / totals.impressions) : null)}</th>
                <th>{rubExact(totals.clicks ? Math.floor(totals.cost / totals.clicks) : null)}</th>
                <th>{rub(totals.cost)}</th>
                <th>{count(totals.conversions)}</th>
                <th>{rub(totals.conversions ? Math.floor(totals.cost / totals.conversions) : null)}</th>
              </tr>
            </tfoot>
          </table>
        </div>
        <p className="cab-muted cab-note">
          Расходы — из отчётов Яндекса и совпадают с кабинетом; мы их не пересчитываем. Обращения — цели кода статистики, модель атрибуции «последний значимый переход».
          Клиентов Метрика не видит: раз в неделю спросим, сколько обращений стали клиентами.
        </p>
      </section>
    </>
  );
}
