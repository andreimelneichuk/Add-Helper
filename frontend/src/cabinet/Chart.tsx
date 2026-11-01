import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Day, Range } from "../api";
import { count, percent, rub, rubExact, shortDate, weekday } from "../format";
import { cpa, cpc, ctr, METRIC_LABEL, type Metric } from "./Metrics";
import { buckets, fillDays } from "./period";

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * power >= value) ?? 10;
  return step * power;
}

const H = 240;
const PAD = { top: 14, right: 44, bottom: 28, left: 64 };

// Ширина графика равна ширине блока: подписи остаются мелкими, а высота постоянной.
function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(([entry]) => entry && entry.contentRect.width > 0 && setWidth(Math.round(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

// Столбцы — выбранный показатель, линия — второй. Наведение, касание или стрелки
// показывают все показатели дня.
export function DailyChart({
  days,
  range,
  metric,
  line = "conversions",
}: {
  days: Day[];
  range: Range;
  metric: Metric;
  line?: Metric | null;
}) {
  const [active, setActive] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const { ref: area, width } = useWidth(720);
  const W = Math.max(300, width);
  if (days.length === 0) return <p className="cab-empty">За выбранные дни статистики нет.</p>;

  const data = buckets(fillDays(days, range));
  const weekly = data.some((b) => b.start !== b.end);
  const values = data.map((b) => b.totals[metric]);
  const top = niceMax(Math.max(...values));
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / data.length;
  const bar = Math.max(2, Math.min(36, slot * 0.64));
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;
  const lineMetric = line && line !== metric ? line : null;
  // Счётные величины делим на четыре целых шага, чтобы на шкале не было дробей.
  const lineRaw = lineMetric ? niceMax(Math.max(...data.map((b) => b.totals[lineMetric]))) : 1;
  const lineTop = lineRaw % 4 === 0 ? lineRaw : Math.ceil(lineRaw / 4) * 4;
  const ly = (v: number) => PAD.top + innerH - (v / lineTop) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  // Подпись дня занимает около 60 px: на узком экране подписываем реже.
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(2, Math.floor(innerW / 60))));
  const cx = (i: number) => PAD.left + slot * i + slot / 2;
  const points = lineMetric ? data.map((b, i) => `${cx(i)},${ly(b.totals[lineMetric])}`).join(" ") : "";
  // Новый показатель или период — новая анимация роста столбцов.
  const motion = `${metric}|${range.since}|${range.until}`;

  const pick = (event: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box || box.width === 0) return;
    const x = ((event.clientX - box.left) / box.width) * W;
    const index = Math.floor((x - PAD.left) / slot);
    setActive(Math.min(data.length - 1, Math.max(0, index)));
  };
  const keys = (event: KeyboardEvent<HTMLDivElement>) => {
    const lastIndex = data.length - 1;
    const moves: Record<string, number | null> = {
      ArrowLeft: Math.max(0, (active ?? lastIndex + 1) - 1),
      ArrowRight: Math.min(lastIndex, (active ?? -1) + 1),
      Home: 0,
      End: lastIndex,
      Escape: null,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    setActive(moves[event.key] ?? null);
  };

  const current = active === null ? null : data[active];
  // Подсказка сбоку от столбца, чтобы не закрывать его: справа в левой половине, слева в правой.
  const side = active !== null && active >= data.length / 2 ? " cab-tip-right" : "";

  return (
    <figure className="cab-chart">
      <div
        ref={area}
        className="cab-chart-area"
        tabIndex={0}
        aria-label="График по дням: стрелки влево и вправо выбирают день"
        onKeyDown={keys}
        onBlur={() => setActive(null)}
      >
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`${METRIC_LABEL[metric]} по ${weekly ? "неделям" : "дням"}`}
          onPointerMove={pick}
          onPointerDown={pick}
          onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}
        >
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
          {current && active !== null && (
            <rect x={PAD.left + slot * active} y={PAD.top} width={slot} height={innerH} className="cab-hover-band" />
          )}
          <g key={motion} className={active === null ? "cab-bars" : "cab-bars cab-bars-focus"}>
            {data.map((b, i) => {
              const value = b.totals[metric];
              const weekend = !weekly && ["сб", "вс"].includes(weekday(b.start));
              const classes = ["cab-bar", weekend && "cab-bar-weekend", i === active && "cab-bar-active"].filter(Boolean).join(" ");
              return (
                <rect
                  key={b.start}
                  x={cx(i) - bar / 2}
                  y={y(value)}
                  width={bar}
                  height={Math.max(0, PAD.top + innerH - y(value))}
                  rx={Math.min(3, bar / 3)}
                  className={classes}
                  style={{ animationDelay: `${Math.min(i * 14, 420)}ms` }}
                />
              );
            })}
          </g>
          {data.map(
            (b, i) =>
              i % labelEvery === 0 && (
                <text key={b.start} x={cx(i)} y={H - 8} textAnchor="middle" className="cab-axis">
                  {shortDate(b.start)}
                </text>
              ),
          )}
          {lineMetric && (
            <g key={`line-${motion}`}>
              <polyline points={points} pathLength={1} className="cab-line" />
              {data.map((b, i) => (
                <circle
                  key={b.start}
                  cx={cx(i)}
                  cy={ly(b.totals[lineMetric])}
                  r={i === active ? 5 : data.length > 40 ? 0 : 3}
                  className="cab-dot"
                />
              ))}
            </g>
          )}
        </svg>
        {current && active !== null && (
          <div
            className={`cab-tip${side}`}
            style={{ left: `${(cx(active) / W) * 100}%` }}
            role="status"
            aria-live="polite"
          >
            <strong>
              {current.start === current.end
                ? `${shortDate(current.start)}, ${weekday(current.start)}`
                : `${shortDate(current.start)} — ${shortDate(current.end)}`}
            </strong>
            <dl>
              <TipRow label="Расход" value={rub(current.totals.cost)} on={metric === "cost"} />
              <TipRow label="Показы" value={count(current.totals.impressions)} on={metric === "impressions"} />
              <TipRow label="Клики" value={count(current.totals.clicks)} on={metric === "clicks"} />
              <TipRow label="CTR" value={percent(ctr(current.totals))} />
              <TipRow label="Цена клика" value={rubExact(cpc(current.totals))} />
              <TipRow label="Обращения" value={count(current.totals.conversions)} on={metric === "conversions"} />
              <TipRow label="Цена обращения" value={rub(cpa(current.totals))} />
            </dl>
          </div>
        )}
      </div>
      <figcaption className="cab-legend">
        <span><i className="cab-key cab-key-bar" />{METRIC_LABEL[metric]}{metric === "cost" ? ", ₽" : ""}</span>
        {lineMetric && <span><i className="cab-key cab-key-line" />{METRIC_LABEL[lineMetric]} (правая шкала)</span>}
        {weekly ? <span>Период длиннее 3 месяцев: столбец — неделя</span> : <span><i className="cab-key cab-key-weekend" />Выходные</span>}
      </figcaption>
    </figure>
  );
}

function TipRow({ label, value, on }: { label: string; value: string; on?: boolean }) {
  return (
    <div className={on ? "cab-tip-on" : undefined}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
