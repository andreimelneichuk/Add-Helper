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
const percent = (bp: number): string => `${bp > 0 ? "+" : bp < 0 ? "−" : ""}${Math.abs(Math.round(bp / 100))}%`;

/** Итог автопилота за сутки: что сделал и что получилось. Ничего не меняет. */
export function AutopilotReport({ store }: { store: Store }) {
  const s = useStoreState(store);
  const [report, setReport] = useState<Report | null>(null);
  const businessName = s.live?.name ?? null;
  useEffect(() => {
    const api = liveApi(store);
    if (!api || typeof api.autopilotReport !== "function") return;
    let alive = true;
    const read = () => api.autopilotReport().then((r) => { if (alive) setReport(r); }, () => undefined);
    void read();
    window.addEventListener(AUTOPILOT_CHANGED, read);
    return () => { alive = false; window.removeEventListener(AUTOPILOT_CHANGED, read); };
  }, [store, businessName]);
  if (!report) return null;
  const judged = report.better + report.same + report.worse;
  const named = report.outcomes.filter((o) => o.verdict === "worse" || o.rolled_back);
  return (
    <section aria-labelledby="ov-auto-title" className="ah-panel ov-auto">
      <h2 id="ov-auto-title" className="ah-panel-title">Что автопилот сделал за сутки</h2>
      {report.done === 0 && report.outcomes.length === 0 ? (
        <p className="ah-muted">За сутки автопилот ничего не менял. Это нормально: он вмешивается, только когда данных хватает.</p>
      ) : (
        <>
          <p className="ov-auto-sum">
            Сделано: {times(report.done)}.{" "}
            {judged > 0
              ? `Уже видно результат у ${judged}: лучше ${report.better}, без изменений ${report.same}, хуже ${report.worse}.`
              : "Результата пока не видно: данных мало, оценю позже."}
            {report.insufficient > 0 && judged > 0 ? ` У ещё ${report.insufficient} данных мало.` : ""}
          </p>
          {report.rolled_back > 0 && (
            <p className="ov-auto-sum">Вернул назад сам: {report.rolled_back}. После остановки цена обращения выросла, поэтому объявление снова в показе.</p>
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
