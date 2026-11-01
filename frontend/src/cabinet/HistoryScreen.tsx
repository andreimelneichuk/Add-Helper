import { useEffect, useState } from "react";
import type { Api, HistoryLine, Overview } from "../api";
import { dateTime, rub } from "../format";
import { CHANGE_KIND, CHANGE_STATUS, SOURCE, VERDICT } from "./words";

function campaignName(target: string, overview: Overview | null): string {
  const id = target.replace(/^campaign:/, "");
  const found = overview?.campaigns.find((c) => c.campaign_id === id);
  return found ? `«${found.name}»` : `№ ${id}`;
}

function value(key: string, raw: unknown): string {
  if (key === "limit" && typeof raw === "number") return `${rub(raw)} в день`;
  if (key === "state") return raw === "on" ? "идут показы" : raw === "suspended" ? "остановлено" : String(raw);
  if (key === "cost_micros" && typeof raw === "number") return rub(raw);
  return String(raw);
}

export function describeChange(line: HistoryLine, overview: Overview | null): { title: string; detail: string[] } {
  const name = campaignName(line.target, overview);
  if (line.ref.startsWith("decision-")) {
    const verdict = VERDICT[line.kind] ?? line.kind;
    const inputs = line.before;
    const facts = [
      typeof inputs.cost_micros === "number" ? `расход ${rub(inputs.cost_micros)}` : null,
      typeof inputs.conversions === "number" ? `обращений ${inputs.conversions}` : null,
    ].filter(Boolean);
    return { title: `Оценка ${name}: ${verdict.toLowerCase()}`, detail: facts.length ? [`Учтено: ${facts.join(", ")}.`] : [] };
  }
  const kind = CHANGE_KIND[line.kind] ?? line.kind;
  const keys = Object.keys(line.after ?? line.before);
  const diff = keys.map((key) => `${value(key, line.before[key])} → ${value(key, line.after?.[key])}`);
  return { title: `${kind}: ${name}`, detail: diff };
}

const FILTERS = ["all", "autopilot", "budget_guard", "user"] as const;
type Filter = (typeof FILTERS)[number];

export function HistoryScreen({ api, overview }: { api: Api; overview: Overview | null }) {
  const [lines, setLines] = useState<HistoryLine[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.history().then(setLines, (e: Error) => setError(e.message));
  }, [api]);
  const shown = lines?.filter((l) => filter === "all" || l.source === filter) ?? [];

  return (
    <>
      <div className="cab-head">
        <h1>История</h1>
        <p className="cab-muted cab-period">Все изменения рекламы и решения автопилота. Записи не стираются: отмена — это новая запись.</p>
      </div>
      <section className="cab-panel">
        <div className="cab-tabs" role="tablist" aria-label="Кто сделал">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={filter === f}
              className={filter === f ? "cab-tab cab-tab-active" : "cab-tab"}
              onClick={() => setFilter(f)}
            >
              {f === "all" ? "Все" : SOURCE[f]}
              <span className="cab-muted"> {lines ? lines.filter((l) => f === "all" || l.source === f).length : ""}</span>
            </button>
          ))}
        </div>
        {error && <p className="cab-bad" role="alert">{error}</p>}
        {lines === null && !error && <p className="cab-empty">Загружаем…</p>}
        {lines !== null && shown.length === 0 && <p className="cab-empty">Записей нет.</p>}
        <ol className="cab-history">
          {shown.map((line) => {
            const text = describeChange(line, overview);
            const isDecision = line.ref.startsWith("decision-");
            return (
              <li key={line.ref} className="cab-history-item">
                <div className="cab-history-meta">
                  <time dateTime={line.at}>{dateTime(line.at)}</time>
                  <span className={`cab-source cab-source-${line.source}`}>{SOURCE[line.source] ?? line.source}</span>
                </div>
                <div className="cab-history-body">
                  <h3>{text.title}</h3>
                  {line.explanation && !isDecision && <p>{line.explanation}</p>}
                  {text.detail.map((d) => <p key={d} className="cab-diff">{d}</p>)}
                  <p className="cab-muted">
                    {isDecision ? `Правила ${line.rules_version ?? "—"}` : CHANGE_STATUS[line.status] ?? line.status}
                    {!isDecision && " · Отмена появится после подключения Яндекс Директа"}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      </section>
    </>
  );
}
