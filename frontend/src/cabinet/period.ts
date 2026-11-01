// Периоды отчёта. Дни считаем по московскому времени, как Директ.
import type { Day, Range, Totals } from "../api";
import { shortDate } from "../format";

export const MAX_DAYS = 366;

function shift(iso: string, days: number): string {
  const moment = new Date(`${iso}T12:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + days);
  return moment.toISOString().slice(0, 10);
}

export function length(range: Range): number {
  return Math.round((Date.parse(`${range.until}T12:00:00Z`) - Date.parse(`${range.since}T12:00:00Z`)) / 86_400_000) + 1;
}

export function moscowToday(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
}

export type Preset = { id: string; label: string; range: (today: string) => Range };

const last = (n: number) => (today: string) => ({ since: shift(today, 1 - n), until: today });

export const PRESETS: Preset[] = [
  { id: "today", label: "Сегодня", range: (today) => ({ since: today, until: today }) },
  { id: "yesterday", label: "Вчера", range: (today) => ({ since: shift(today, -1), until: shift(today, -1) }) },
  { id: "7", label: "7 дней", range: last(7) },
  { id: "14", label: "14 дней", range: last(14) },
  { id: "30", label: "30 дней", range: last(30) },
  { id: "month", label: "Этот месяц", range: (today) => ({ since: `${today.slice(0, 8)}01`, until: today }) },
  {
    id: "prev-month",
    label: "Прошлый месяц",
    range: (today) => {
      const end = shift(`${today.slice(0, 8)}01`, -1);
      return { since: `${end.slice(0, 8)}01`, until: end };
    },
  },
  { id: "90", label: "90 дней", range: last(90) },
];

export function presetOf(range: Range, today: string): Preset | undefined {
  return PRESETS.find((p) => {
    const r = p.range(today);
    return r.since === range.since && r.until === range.until;
  });
}

const DAYS_WORD = (n: number) => {
  const tail = n % 100;
  if (tail >= 11 && tail <= 14) return "дней";
  if (n % 10 === 1) return "день";
  if (n % 10 >= 2 && n % 10 <= 4) return "дня";
  return "дней";
};

export function rangeLabel(range: Range): string {
  const n = length(range);
  const dates = range.since === range.until ? shortDate(range.since) : `${shortDate(range.since)} — ${shortDate(range.until)}`;
  return `${dates} · ${n} ${DAYS_WORD(n)}`;
}

export const EMPTY: Totals = { impressions: 0, clicks: 0, cost: 0, conversions: 0 };

// Сервис присылает только дни со статистикой; для графика нужны все дни периода.
export function fillDays(days: Day[], range: Range): Day[] {
  const known = new Map(days.map((d) => [d.day, d.totals]));
  const out: Day[] = [];
  for (let day = range.since; day <= range.until; day = shift(day, 1)) {
    out.push({ day, totals: known.get(day) ?? EMPTY });
  }
  return out;
}

export type Bucket = { start: string; end: string; totals: Totals };

// Длинный период на графике собираем по неделям, иначе столбцы тоньше пикселя.
export function buckets(days: Day[]): Bucket[] {
  const size = days.length > 92 ? 7 : 1;
  const out: Bucket[] = [];
  for (let i = 0; i < days.length; i += size) {
    const chunk = days.slice(i, i + size);
    out.push({ start: chunk[0]!.day, end: chunk[chunk.length - 1]!.day, totals: sum(chunk.map((d) => d.totals)) });
  }
  return out;
}

export function sum(lines: Totals[]): Totals {
  return lines.reduce(
    (acc, t) => ({
      impressions: acc.impressions + t.impressions,
      clicks: acc.clicks + t.clicks,
      cost: acc.cost + t.cost,
      conversions: acc.conversions + t.conversions,
    }),
    EMPTY,
  );
}
