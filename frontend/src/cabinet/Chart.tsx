import type { Day, Totals } from "../api";
import { count, rub, shortDate, weekday } from "../format";

export type Metric = "cost" | "clicks" | "impressions" | "conversions";

export const METRIC_LABEL: Record<Metric, string> = {
  cost: "Расход",
  clicks: "Клики",
  impressions: "Показы",
  conversions: "Обращения",
};

const show = (metric: Metric, value: number) => (metric === "cost" ? rub(value) : count(value));

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * power >= value) ?? 10;
  return step * power;
}

const W = 720;
const H = 220;
const PAD = { top: 12, right: 44, bottom: 28, left: 64 };

// Столбцы — основной показатель, линия — обращения. Подписи осей короткие, значения в подсказке.
export function DailyChart({ days, metric, line = "conversions" }: { days: Day[]; metric: Metric; line?: Metric | null }) {
  if (days.length === 0) return <p className="cab-empty">За выбранные дни статистики нет.</p>;
  const values = days.map((d) => d.totals[metric]);
  const top = niceMax(Math.max(...values));
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / days.length;
  const bar = Math.max(4, slot * 0.62);
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;
  const lineMetric = line && line !== metric ? line : null;
  // Счётные величины делим на четыре целых шага, чтобы на шкале не было дробей.
  const lineRaw = lineMetric ? niceMax(Math.max(...days.map((d) => d.totals[lineMetric]))) : 1;
  const lineTop = lineRaw % 4 === 0 ? lineRaw : Math.ceil(lineRaw / 4) * 4;
  const ly = (v: number) => PAD.top + innerH - (v / lineTop) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const labelEvery = days.length > 10 ? 2 : 1;
  const points = lineMetric
    ? days.map((d, i) => `${PAD.left + slot * i + slot / 2},${ly(d.totals[lineMetric])}`).join(" ")
    : "";

  return (
    <figure className="cab-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${METRIC_LABEL[metric]} по дням`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(top * t)} y2={y(top * t)} className="cab-grid" />
            <text x={PAD.left - 8} y={y(top * t) + 4} textAnchor="end" className="cab-axis">
              {metric === "cost" ? count(Math.round((top * t) / 1_000_000)) : count(Math.round(top * t))}
            </text>
            {lineMetric && (
              <text x={W - PAD.right + 8} y={ly(lineTop * t) + 4} className="cab-axis cab-axis-line">
                {count(Math.round(lineTop * t))}
              </text>
            )}
          </g>
        ))}
        {days.map((d, i) => {
          const value = d.totals[metric];
          const x = PAD.left + slot * i + (slot - bar) / 2;
          const weekend = ["сб", "вс"].includes(weekday(d.day));
          return (
            <g key={d.day}>
              <rect x={x} y={y(value)} width={bar} height={Math.max(0, PAD.top + innerH - y(value))} rx={2} className={weekend ? "cab-bar cab-bar-weekend" : "cab-bar"}>
                <title>{`${shortDate(d.day)}, ${weekday(d.day)}: ${METRIC_LABEL[metric].toLowerCase()} ${show(metric, value)}${lineMetric ? `, ${METRIC_LABEL[lineMetric].toLowerCase()} ${count(d.totals[lineMetric])}` : ""}`}</title>
              </rect>
              {i % labelEvery === 0 && (
                <text x={PAD.left + slot * i + slot / 2} y={H - 8} textAnchor="middle" className="cab-axis">
                  {shortDate(d.day)}
                </text>
              )}
            </g>
          );
        })}
        {lineMetric && <polyline points={points} className="cab-line" />}
        {lineMetric &&
          days.map((d, i) => (
            <circle key={d.day} cx={PAD.left + slot * i + slot / 2} cy={ly(d.totals[lineMetric])} r={3} className="cab-dot" />
          ))}
      </svg>
      <figcaption className="cab-legend">
        <span><i className="cab-key cab-key-bar" />{METRIC_LABEL[metric]}{metric === "cost" ? ", ₽" : ""}</span>
        {lineMetric && <span><i className="cab-key cab-key-line" />{METRIC_LABEL[lineMetric]} (правая шкала)</span>}
        <span><i className="cab-key cab-key-weekend" />Выходные</span>
      </figcaption>
    </figure>
  );
}

export function sumTotals(days: Day[]): Totals {
  return days.reduce(
    (acc, d) => ({
      impressions: acc.impressions + d.totals.impressions,
      clicks: acc.clicks + d.totals.clicks,
      cost: acc.cost + d.totals.cost,
      conversions: acc.conversions + d.totals.conversions,
    }),
    { impressions: 0, clicks: 0, cost: 0, conversions: 0 },
  );
}
