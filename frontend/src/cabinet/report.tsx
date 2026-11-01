import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Range } from "../api";
import { dateTime } from "../format";
import { PeriodPicker } from "./PeriodPicker";

// Период отчёта общий для всех экранов: выбрали на сводке — он же в статистике и рекламе.
export type Report = {
  range: Range;
  today: string;
  asOf: string | null;
  loading: boolean;
  setRange: (range: Range) => void;
};

export const ReportContext = createContext<Report | null>(null);

export function useReport(): Report {
  const report = useContext(ReportContext);
  if (!report) throw new Error("Экран отчёта вне кабинета");
  return report;
}

export type Crumb = { label: string; href?: string };

export function ReportHead({ title, crumbs = [], aside }: { title: ReactNode; crumbs?: Crumb[]; aside?: ReactNode }) {
  const { range, today, asOf, setRange } = useReport();
  return (
    <div className="cab-head">
      {crumbs.length > 0 && (
        <nav className="cab-crumbs" aria-label="Путь">
          {crumbs.map((c) => (
            <span key={c.label}>
              {c.href ? <a href={c.href}>{c.label}</a> : c.label}
              <span aria-hidden="true"> › </span>
            </span>
          ))}
        </nav>
      )}
      <div className="cab-head-row">
        <div className="cab-title">
          <h1>{title}</h1>
          {aside}
        </div>
        <PeriodPicker range={range} today={today} onChange={setRange} />
      </div>
      <p className="cab-muted cab-period">Статистика Яндекса на {dateTime(asOf)} (МСК). Расходы — из отчётов Яндекса, мы их не пересчитываем.</p>
    </div>
  );
}

// Загрузка отчёта: пока идёт новая, показываем прежние цифры приглушёнными, а не пустой экран.
export function useLoad<T>(load: () => Promise<T>, key: string): { data: T | null; error: string | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    load().then(
      (value) => live && (setData(value), setLoading(false)),
      (e: Error) => live && (setError(e.message), setLoading(false)),
    );
    return () => {
      live = false;
    };
    // load меняется на каждом рендере, поэтому перезагружаем только по ключу.
  }, [key]);
  return { data, error, loading };
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="cab-skeleton" aria-label="Загружаем…" role="status">
      {Array.from({ length: rows }, (_, i) => <span key={i} />)}
    </div>
  );
}
