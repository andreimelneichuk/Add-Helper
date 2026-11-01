import { useCallback, useEffect, useMemo, useState } from "react";
import { createApi, type Business, type Me, type Overview } from "../api";
import { dateTime, rub, shortDate } from "../format";
import { Ads } from "./Ads";
import { Campaigns } from "./Campaigns";
import "./cabinet.css";
import { HistoryScreen } from "./HistoryScreen";
import { Home } from "./Home";
import { NotificationsScreen } from "./NotificationsScreen";
import { ProfileScreen } from "./ProfileScreen";
import { Stats } from "./Stats";

export type Section = "home" | "campaigns" | "stats" | "ads" | "history" | "profile" | "notify";

const NAV: { id: Section; label: string; icon: string }[] = [
  { id: "home", label: "Сводка", icon: "M3 13h8V3H3zm10 8h8V11h-8zM3 21h8v-6H3zm10-18v6h8V3z" },
  { id: "campaigns", label: "Реклама", icon: "M4 5h16v2H4zm0 6h16v2H4zm0 6h10v2H4z" },
  { id: "stats", label: "Статистика", icon: "M5 20V10h3v10zm5.5 0V4h3v16zM16 20v-7h3v7z" },
  { id: "ads", label: "Объявления", icon: "M4 4h16v12H7l-3 3zm3 4v2h10V8zm0 4v2h7v-2z" },
  { id: "history", label: "История", icon: "M12 4a8 8 0 1 1-8 8h2a6 6 0 1 0 1.8-4.3L10 10H4V4l2.3 2.3A8 8 0 0 1 12 4m-1 4h2v4.6l3 1.8-1 1.7-4-2.4z" },
  { id: "notify", label: "Уведомления", icon: "M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2m6-6V11c0-3.1-1.6-5.6-4.5-6.3V4a1.5 1.5 0 0 0-3 0v.7C7.6 5.4 6 7.9 6 11v5l-2 2v1h16v-1z" },
  { id: "profile", label: "О бизнесе", icon: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8m0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5" },
];

function sectionFromHash(): Section {
  const id = window.location.hash.slice(1);
  return NAV.some((item) => item.id === id) ? (id as Section) : "home";
}

export function Cabinet({ me, business }: { me: Me; business: Business }) {
  const api = useMemo(() => createApi(business.id, me.csrf_token), [business.id, me.csrf_token]);
  const [section, setSection] = useState<Section>(sectionFromHash);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.overview().then(setOverview, (e: Error) => setError(e.message));
  }, [api]);

  useEffect(reload, [reload]);
  useEffect(() => {
    const onHash = () => setSection(sectionFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const money = overview?.money[0];
  const go = (next: Section) => {
    window.location.hash = next;
    setSection(next);
    window.scrollTo(0, 0);
  };

  return (
    <div className="cab">
      <header className="cab-top">
        <div className="cab-brand">
          <span className="cab-logo" aria-hidden="true">A</span>
          <span>Ad Helper</span>
        </div>
        <div className="cab-business">
          <strong>{business.name}</strong>
          {money && <span className="cab-muted">Кабинет Директа: {money.period.account}</span>}
        </div>
        <div className="cab-top-right">
          {money && (
            <div className="cab-balance" title={`Прочитано ${dateTime(money.period.balance_checked_at)}`}>
              <span className="cab-muted">На счёте</span>
              <strong>{rub(money.period.last_balance)}</strong>
            </div>
          )}
          <span className="cab-user">{me.email ?? "Владелец"}</span>
        </div>
      </header>
      <div className="cab-body">
        <nav className="cab-nav" aria-label="Разделы">
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === section ? "cab-nav-item cab-nav-active" : "cab-nav-item"}
              aria-current={item.id === section ? "page" : undefined}
              onClick={() => go(item.id)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d={item.icon} /></svg>
              <span>{item.label}</span>
              {item.id === "home" && overview && overview.alerts.length > 0 && (
                <span className="cab-badge" aria-label={`Проблем: ${overview.alerts.length}`}>{overview.alerts.length}</span>
              )}
            </button>
          ))}
        </nav>
        <main className="cab-main">
          {error && <p className="cab-alert cab-alert-error" role="alert">Не удалось загрузить данные: {error}</p>}
          {!overview && !error && <p className="cab-empty">Загружаем данные…</p>}
          {overview && section === "home" && <Home api={api} overview={overview} onChanged={reload} go={go} />}
          {overview && section === "campaigns" && <Campaigns overview={overview} />}
          {overview && section === "stats" && <Stats overview={overview} />}
          {section === "ads" && <Ads api={api} />}
          {section === "history" && <HistoryScreen api={api} overview={overview} />}
          {section === "profile" && <ProfileScreen api={api} />}
          {section === "notify" && <NotificationsScreen api={api} />}
        </main>
      </div>
    </div>
  );
}

export function PeriodNote({ overview }: { overview: Overview }) {
  const asOf = overview.money[0]?.period.data_as_of ?? null;
  return (
    <p className="cab-muted cab-period">
      {shortDate(overview.since)} — {shortDate(overview.today)} · 14 дней · статистика Яндекса на {dateTime(asOf)} (МСК)
    </p>
  );
}
