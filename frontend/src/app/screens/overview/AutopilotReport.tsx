import { useEffect, useState } from "react";
import type { AutopilotReport as Report } from "../../../api";
import { AUTOPILOT_CHANGED } from "../../autopilotEvents";
import { liveApi } from "../../live";
import type { Store } from "../../store";
import { adName, useStoreState } from "../../store";

const VERDICT: Record<string, string> = { better: "стало лучше", same: "без заметных изменений", worse: "стало хуже", insufficient: "данных мало" };

function times(n: number): string {
  const rest = n % 100 > 10 && n % 100 < 20 ? 5 : n % 10;
  return `${n} ${rest === 1 ? "действие" : rest >= 2 && rest <= 4 ? "действия" : "действий"}`;
}

/** Базисные пункты в проценты только для показа; сам расчёт делает сервис. */
const percent = (bp: number): string => {
  const n = Number.isFinite(bp) ? Math.round(Math.abs(bp) / 100) : 0;
  return `${n > 0 ? (bp > 0 ? "+" : "−") : ""}${n}%`;
};
const count = (n: number | undefined): number => (Number.isFinite(n) ? (n as number) : 0);

/** Итог автопилота за сутки: что сделал и что получилось. Ничего не меняет. */
export function AutopilotReport({ store }: { store: Store }) {
  const s = useStoreState(store);
  const [report, setReport] = useState<Report | null>(null);
  const businessKey = s.live?.name ?? null;
  useEffect(() => {
    setReport(null);
    const api = liveApi(store);
    if (!api || typeof api.autopilotReport !== "function") return;
    let alive = true;
    const read = () => api.autopilotReport().then((r) => { if (alive) setReport(r); }, () => { if (alive) setReport(null); });
    void read();
    window.addEventListener(AUTOPILOT_CHANGED, read);
    return () => { alive = false; window.removeEventListener(AUTOPILOT_CHANGED, read); };
  }, [store, businessKey]);
  if (!report) return null;
  const outcomes = report.outcomes ?? [];
  const done = count(report.done);
  const better = count(report.better);
  const same = count(report.same);
  const worse = count(report.worse);
  const insufficient = count(report.insufficient);
  const rolledBack = count(report.rolled_back);
  const judged = better + same + worse;
  const named = outcomes.filter((o) => o.verdict === "worse" || o.rolled_back);
  return (
    <section aria-labelledby="ov-auto-title" className="ah-panel ov-auto">
      <h2 id="ov-auto-title" className="ah-panel-title">Что автопилот сделал за сутки</h2>
      {done === 0 && outcomes.length === 0 ? (
        <p className="ah-muted">За сутки автопилот ничего не менял. Это нормально: он вмешивается, только когда данных хватает.</p>
      ) : (
        <>
          <p className="ov-auto-sum">
            {done > 0 ? `Сделано: ${times(done)}.` : "Новых действий за сутки не было."}{" "}
            {judged > 0
              ? `Уже видно результат у ${judged}: лучше ${better}, без изменений ${same}, хуже ${worse}.`
              : "Результата пока не видно: данных мало, оценю позже."}
            {insufficient > 0 && judged > 0 ? ` У ещё ${insufficient} данных мало.` : ""}
          </p>
          {rolledBack > 0 && (
            <p className="ov-auto-sum">Вернул назад сам: {rolledBack}. Объявление снова в показе: после остановки результат стал хуже. Отменить можно в истории.</p>
          )}
          {named.length > 0 && (
            <ul className="ov-auto-list">
              {named.map((o) => {
                const ad = s.ads.find((a) => o.object_ref === `ad:${a.id.split("/").pop()}`);
                return (
                  <li key={o.change_id}>
                    {ad ? `«${adName(s, ad.id)}»` : "Объявление"}: {VERDICT[o.verdict] ?? "оценено"}
                    {o.cpl_change_bp != null ? `, цена обращения ${percent(o.cpl_change_bp)}` : ""}
                    {o.rolled_back ? ", возвращено в показ" : ""}.
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      <button type="button" className="ah-btn ov-auto-all" onClick={() => { store.set({ filter: "autopilot" }); store.goView("history"); }}>Посмотреть и отменить</button>
    </section>
  );
}
