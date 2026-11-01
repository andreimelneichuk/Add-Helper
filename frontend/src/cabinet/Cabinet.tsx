import { useCallback, useEffect, useMemo, useState } from "react";
import { createApi, type Business, type Me, type Overview, type Range } from "../api";
import { dateTime, rub } from "../format";
import { Ads, CreativeScreen } from "./Ads";
import { Campaigns } from "./Campaigns";
import { AdScreen, CampaignScreen } from "./Details";
import "./cabinet.css";
import { HistoryScreen } from "./HistoryScreen";
import { Home } from "./Home";
import { NotificationsScreen } from "./NotificationsScreen";
import { moscowToday } from "./period";
import { ProfileScreen } from "./ProfileScreen";
import { ReportContext, type Report } from "./report";
import { formatHash, navSection, parseHash, type Route, type Section } from "./route";
import { Stats } from "./Stats";

export type { Section } from "./route";

const NAV: { id: Section; label: string; icon: string }[] = [
  { id: "home", label: "Сводка", icon: "M3 13h8V3H3zm10 8h8V11h-8zM3 21h8v-6H3zm10-18v6h8V3z" },
  { id: "campaigns", label: "Реклама", icon: "M4 5h16v2H4zm0 6h16v2H4zm0 6h10v2H4z" },
  { id: "stats", label: "Статистика", icon: "M5 20V10h3v10zm5.5 0V4h3v16zM16 20v-7h3v7z" },
  { id: "ads", label: "Объявления", icon: "M4 4h16v12H7l-3 3zm3 4v2h10V8zm0 4v2h7v-2z" },
  { id: "history", label: "История", icon: "M12 4a8 8 0 1 1-8 8h2a6 6 0 1 0 1.8-4.3L10 10H4V4l2.3 2.3A8 8 0 0 1 12 4m-1 4h2v4.6l3 1.8-1 1.7-4-2.4z" },
  { id: "notify", label: "Уведомления", icon: "M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2m6-6V11c0-3.1-1.6-5.6-4.5-6.3V4a1.5 1.5 0 0 0-3 0v.7C7.6 5.4 6 7.9 6 11v5l-2 2v1h16v-1z" },
  { id: "profile", label: "О бизнесе", icon: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8m0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5" },
];

export function Cabinet({ me, business }: { me: Me; business: Business }) {
  const api = useMemo(() => createApi(business.id, me.csrf_token), [business.id, me.csrf_token]);
  const [place, setPlace] = useState(() => parseHash(window.location.hash));
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { route, range: chosen } = place;
  const rangeKey = chosen ? `${chosen.since}|${chosen.until}` : "";

  const reload = useCallback(() => {
    setLoading(true);
    api.overview(chosen).then(
      (next) => {
        setOverview(next);
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        setError(e.message);
        setLoading(false);
      },
    );
    // Период сравниваем по значению: новый объект с теми же датами не перезагружает отчёт.
  }, [api, rangeKey]);

  useEffect(reload, [reload]);
  useEffect(() => {
    const onHash = () => setPlace(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (section: Section) => {
    const next: Route = { section };
    window.location.hash = formatHash(next, chosen);
    setPlace({ route: next, range: chosen });
    window.scrollTo(0, 0);
  };
  const setRange = (range: Range) => {
    // Смена периода не новый шаг: «Назад» ведёт на прошлый экран, а не на прошлый период.
    window.history.replaceState(null, "", formatHash(route, range));
    setPlace({ route, range });
  };

  const money = overview?.money[0];
  const today = overview?.today ?? moscowToday();
  const report: Report = {
    range: chosen ?? (overview ? { since: overview.since, until: overview.until } : { since: today, until: today }),
    today,
    asOf: money?.period.data_as_of ?? null,
    loading,
    setRange,
  };
  const active = navSection(route);
  const screen =
    route.section === "campaign" || route.section === "ad"
      ? `${route.section}/${route.account}/${route.id}`
      : route.section === "creative"
        ? `creative/${route.id}`
        : route.section;

  return (
    <ReportContext.Provider value={report}>
      <div className="cab">
        <div className={loading && overview ? "cab-progress cab-progress-on" : "cab-progress"} aria-hidden="true" />
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
                className={item.id === active ? "cab-nav-item cab-nav-active" : "cab-nav-item"}
                aria-current={item.id === active ? "page" : undefined}
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
            <div key={screen} className="cab-screen">
              {overview && route.section === "home" && <Home api={api} overview={overview} onChanged={reload} go={go} />}
              {overview && route.section === "campaigns" && <Campaigns overview={overview} api={api} />}
              {overview && route.section === "stats" && <Stats overview={overview} />}
              {overview && route.section === "campaign" && <CampaignScreen api={api} account={route.account} id={route.id} />}
              {overview && route.section === "ad" && <AdScreen api={api} account={route.account} id={route.id} />}
              {route.section === "ads" && <Ads api={api} overview={overview} />}
              {route.section === "creative" && <CreativeScreen api={api} id={route.id} />}
              {route.section === "history" && <HistoryScreen api={api} overview={overview} />}
              {route.section === "profile" && <ProfileScreen api={api} />}
              {route.section === "notify" && <NotificationsScreen api={api} />}
            </div>
          </main>
        </div>
      </div>
    </ReportContext.Provider>
  );
}
