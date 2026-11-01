import type { Overview } from "../api";
import { DaysTable, Dim, MetricChart } from "./Details";
import { ReportHead, useReport } from "./report";

export function Stats({ overview }: { overview: Overview }) {
  const { range, loading } = useReport();
  return (
    <>
      <ReportHead title="Статистика" />
      <Dim loading={loading}>
        <section className="cab-panel">
          <MetricChart days={overview.days} previous={overview.previous} range={range} />
        </section>
        <section className="cab-panel">
          <h2>По дням</h2>
          <DaysTable days={overview.days} range={range} />
          <p className="cab-muted cab-note">
            Обращения — цели кода статистики, модель атрибуции «последний значимый переход». Клиентов Метрика не видит: раз в неделю спросим,
            сколько обращений стали клиентами. Стрелки у показателей сравнивают период с тем же числом дней до него.
          </p>
        </section>
      </Dim>
    </>
  );
}
