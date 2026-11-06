// Статистика на настоящих данных: свой период и отчёт по направлению.
// Цифры берём из ответа сервиса как есть; считаем только цену обращения для показа.
import { useEffect, useState } from "react";
import type { Api, CampaignDetail, Range } from "../../api";
import { CAMPAIGN_STATE } from "../words";
import { leadsWord, rub } from "../data";
import type { PeriodStats } from "../data";
import { dayAt, dayNumber, periodStats, rubles } from "../live";

// Сервис отдаёт не больше года за раз.
const MAX_DAYS = 366;

const message = (e: unknown) => (e instanceof Error ? e.message : "Сервис не ответил. Попробуйте позже.");
const short = (day: string) =>
  new Date(`${day}T12:00:00Z`)
    .toLocaleDateString("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" })
    .replace(/\.$/, "");

/** Подпись периода: «3–10 нояб.» или один день. */
export const rangeLabel = (r: Range) => (r.since === r.until ? short(r.since) : `${short(r.since)} – ${short(r.until)}`);

/** Что не так с периодом, словами владельца; null, если всё в порядке. */
export function rangeProblem(r: Range, today: string): string | null {
  if (!r.since || !r.until) return "Укажите обе даты.";
  if (r.since > r.until) return "Начало позже конца.";
  if (r.until > today) return "Будущих дней в статистике ещё нет.";
  if (dayNumber(r.until) - dayNumber(r.since) + 1 > MAX_DAYS) return "Не больше года за раз.";
  return null;
}

export function OwnPeriod({ api, today, picked }: { api: Api; today: string; picked: (range: Range, stats: PeriodStats) => void }) {
  const [range, setRange] = useState<Range>({ since: dayAt(dayNumber(today) - 29), until: today });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problem = rangeProblem(range, today);
  const show = async () => {
    if (problem) return;
    setBusy(true);
    setError(null);
    try {
      const [overview, ads] = await Promise.all([api.overview(range), api.ads(range)]);
      picked(range, periodStats(rangeLabel(range), overview, range.since, range.until, ads));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="stats-own" aria-label="Свой период" onSubmit={(e) => { e.preventDefault(); void show(); }}>
      <label><span className="ah-label">С</span><input className="ah-input" type="date" max={today} value={range.since} onChange={(e) => setRange({ ...range, since: e.target.value })} /></label>
      <label><span className="ah-label">По</span><input className="ah-input" type="date" max={today} value={range.until} onChange={(e) => setRange({ ...range, until: e.target.value })} /></label>
      <button type="submit" className="ah-btn ah-btn-primary" disabled={busy || !!problem}>Показать</button>
      {(problem || error) && <p className="ah-error stats-own-note" role="alert">{problem ?? error}</p>}
    </form>
  );
}

const price = (cost: number, count: number) => (count > 0 ? rub(cost / count) : "—");

function versus(now: number, before: number, word: (n: number) => string): string {
  if (before === 0) return now === 0 ? "" : "в прошлом таком же периоде не было";
  return `в прошлом таком же периоде ${before} ${word(before)}`;
}

export function DirectionReport({ api, id, range, label, back, openAd }: { api: Api; id: string; range: Range; label: string; back: () => void; openAd: (id: string) => void }) {
  const [report, setReport] = useState<CampaignDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [account, campaign] = id.split("/") as [string, string];
  useEffect(() => {
    setReport(null);
    api.campaign(account, campaign, range).then(setReport, (e: unknown) => setError(message(e)));
  }, [api, account, campaign, range.since, range.until]);

  const head = (
    <div className="stats-dir-head">
      <button type="button" className="ah-btn" onClick={back}>Назад к статистике</button>
    </div>
  );
  if (!report) return <section className="ah-panel">{head}<p className={error ? "ah-error" : "ah-muted"} role={error ? "alert" : undefined}>{error ?? "Загружаю…"}</p></section>;

  const { campaign: c, previous } = report;
  const cost = rubles(c.totals.cost);
  const leads = c.totals.conversions;
  const state = CAMPAIGN_STATE[c.state]?.label ?? c.state;
  const nums: [string, string | number][] = [
    ["Обращения", leads],
    ["Расход", rub(cost)],
    ["Цена обращения", price(cost, leads)],
    ["Клики", c.totals.clicks.toLocaleString("ru-RU")],
    ["Цена клика", price(cost, c.totals.clicks)],
    ["Показы", c.totals.impressions.toLocaleString("ru-RU")],
  ];
  const compare = versus(leads, previous.conversions, leadsWord);
  return (
    <section className="ah-panel stats-dir" aria-labelledby="stats-dir-title">
      {head}
      <h2 id="stats-dir-title" className="ah-panel-title">{c.name}</h2>
      <p className="stats-note">{label}. {state}{c.limit !== null ? `, лимит в день ${rub(rubles(c.limit))}` : ""}.{compare ? ` ${compare[0]!.toUpperCase()}${compare.slice(1)}.` : ""}</p>
      <dl className="stats-nums">
        {nums.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}
      </dl>
      {report.ads.length === 0 ? <p className="ah-muted">За этот период у направления не было объявлений с показами.</p> : (
        <div className="ah-table-wrap" role="region" aria-label="Объявления направления" tabIndex={0}>
          <table className="ah-table">
            <thead><tr><th scope="col">Объявление</th><th scope="col">Расход</th><th scope="col">Обращения</th><th scope="col">Цена</th></tr></thead>
            <tbody>
              {report.ads.map((a) => {
                const adCost = rubles(a.totals.cost);
                return (
                  <tr key={a.ad_id}>
                    <td><button type="button" className="ah-link" aria-label={`Открыть «${a.title}»`} onClick={() => openAd(`${a.account}/${a.ad_id}`)}>{a.title}</button></td>
                    <td>{rub(adCost)}</td><td>{a.totals.conversions}</td><td>{price(adCost, a.totals.conversions)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
