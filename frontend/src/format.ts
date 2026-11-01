// Только показ: деньги остаются целыми микроединицами до последнего шага.
import { MICROS_PER_RUBLE } from "./money";

const NBSP = " ";
const group = (value: number) => value.toLocaleString("ru-RU").replace(/\s/g, NBSP);

export function rub(micros: number | null | undefined): string {
  if (micros === null || micros === undefined) return "—";
  return `${group(Math.round(micros / MICROS_PER_RUBLE))}${NBSP}₽`;
}

// Цена клика: копейки важны, когда клик стоит десятки рублей.
export function rubExact(micros: number | null | undefined): string {
  if (micros === null || micros === undefined) return "—";
  const kopecks = Math.round(micros / 10_000);
  const rubles = Math.trunc(kopecks / 100);
  const rest = String(Math.abs(kopecks % 100)).padStart(2, "0");
  return `${group(rubles)},${rest}${NBSP}₽`;
}

export function count(value: number): string {
  return group(value);
}

export function percent(bp: number | null | undefined): string {
  if (bp === null || bp === undefined) return "—";
  return `${(bp / 100).toFixed(2).replace(".", ",")}${NBSP}%`;
}

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

export function shortDate(iso: string): string {
  const [, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${day}${NBSP}${MONTHS[(month ?? 1) - 1]}`;
}

// Момент времени — день по московскому времени: так считает сутки Директ.
export function moscowDay(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow", day: "numeric", month: "short" }).replace(".", "");
}

export function dateTime(iso: string | null): string {
  if (!iso) return "—";
  const moment = new Date(iso);
  return moment.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function weekday(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T12:00:00Z`).toLocaleDateString("ru-RU", { weekday: "short" });
}
