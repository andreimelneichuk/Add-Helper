import { useEffect, useRef, useState } from "react";
import type { Totals } from "../api";
import { count, percent, rub, rubExact } from "../format";

export type Metric = "cost" | "clicks" | "impressions" | "conversions";

export const METRICS: Metric[] = ["cost", "impressions", "clicks", "conversions"];

export const METRIC_LABEL: Record<Metric, string> = {
  cost: "Расход",
  clicks: "Клики",
  impressions: "Показы",
  conversions: "Обращения",
};

export const showMetric = (metric: Metric, value: number) => (metric === "cost" ? rub(value) : count(value));

// Производные показатели для показа: целочисленное деление, как на сервере.
export const ctr = (t: Totals) => (t.impressions ? Math.floor((t.clicks * 10_000) / t.impressions) : null);
export const cpc = (t: Totals) => (t.clicks ? Math.floor(t.cost / t.clicks) : null);
export const cpa = (t: Totals) => (t.conversions ? Math.floor(t.cost / t.conversions) : null);
export const conversion = (t: Totals) => (t.clicks ? Math.floor((t.conversions * 10_000) / t.clicks) : null);

export function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Число плавно доходит до нового значения. Первый показ — сразу итог: анимируем только перемены.
export function useTween(value: number, ms = 450): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    if (start === value || reducedMotion() || typeof requestAnimationFrame !== "function") {
      from.current = value;
      setShown(value);
      return;
    }
    let frame = 0;
    const began = performance.now();
    const step = (now: number) => {
      const k = Math.min(1, (now - began) / ms);
      const eased = 1 - (1 - k) ** 3;
      const next = Math.round(start + (value - start) * eased);
      from.current = next;
      setShown(next);
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value, ms]);
  return shown;
}

type Tone = "good" | "bad" | "flat";

// Рост расхода сам по себе не хорош и не плох, поэтому он серый.
const GROWTH_IS: Record<Metric | "cpa", Tone> = {
  cost: "flat",
  impressions: "good",
  clicks: "good",
  conversions: "good",
  cpa: "bad",
};

export function Delta({ now, before, kind }: { now: number | null; before: number | null; kind: Metric | "cpa" }) {
  if (now === null || before === null) return <span className="cab-delta cab-delta-flat">не с чем сравнить</span>;
  if (before === 0) return <span className="cab-delta cab-delta-flat">{now === 0 ? "без изменений" : "раньше не было"}</span>;
  const change = Math.round(((now - before) * 100) / before);
  if (change === 0) return <span className="cab-delta cab-delta-flat">≈ как раньше</span>;
  const growth = GROWTH_IS[kind];
  const tone: Tone = growth === "flat" ? "flat" : (change > 0) === (growth === "good") ? "good" : "bad";
  return (
    <span className={`cab-delta cab-delta-${tone}`} title="По сравнению с тем же числом дней до периода">
      {change > 0 ? "▲" : "▼"} {Math.abs(change)} %
    </span>
  );
}

function TabValue({ metric, value }: { metric: Metric; value: number }) {
  return <>{showMetric(metric, useTween(value))}</>;
}

// Вкладки-показатели над графиком: значение за период и сравнение с прошлым.
export function MetricTabs({
  totals,
  previous,
  metric,
  onPick,
}: {
  totals: Totals;
  previous: Totals;
  metric: Metric;
  onPick: (m: Metric) => void;
}) {
  return (
    <div className="cab-mrow">
      <div className="cab-mtabs" role="tablist" aria-label="Показатель на графике">
        {METRICS.map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={metric === m}
            className={metric === m ? `cab-mtab cab-mtab-active cab-mtab-${m}` : `cab-mtab cab-mtab-${m}`}
            onClick={() => onPick(m)}
          >
            <span className="cab-mtab-label">{METRIC_LABEL[m]}</span>
            <span className="cab-mtab-value"><TabValue metric={m} value={totals[m]} /></span>
            <Delta now={totals[m]} before={previous[m]} kind={m} />
          </button>
        ))}
      </div>
      {/* Расчётные показатели на графике не рисуются, поэтому это не вкладки, а сводка рядом. */}
      <dl className="cab-derived" aria-label="Расчётные показатели">
        <div>
          <dt>Цена обращения</dt>
          <dd>{rub(cpa(totals))} <Delta now={cpa(totals)} before={cpa(previous)} kind="cpa" /></dd>
        </div>
        <div>
          <dt>CTR</dt>
          <dd>{percent(ctr(totals))}</dd>
        </div>
        <div>
          <dt>Цена клика</dt>
          <dd>{rubExact(cpc(totals))}</dd>
        </div>
      </dl>
    </div>
  );
}
