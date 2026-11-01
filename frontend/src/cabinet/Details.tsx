import { useState, type ReactNode } from "react";
import type { Api, Day, Range, Totals } from "../api";
import { rub, shortDate, weekday } from "../format";
import { AudiencePanel, BasisPanel } from "./AdFacts";
import { AdTable, metricColumns, StateMark } from "./Campaigns";
import { DailyChart } from "./Chart";
import { MetricTabs, type Metric } from "./Metrics";
import { fillDays, sum } from "./period";
import { ReportHead, Skeleton, useLoad, useReport } from "./report";
import { campaignHref, formatHash } from "./route";
import { SortTable, type Column } from "./Table";

// График с вкладками показателей: общий для статистики, направления и объявления.
export function MetricChart({ days, previous, range }: { days: Day[]; previous: Totals; range: Range }) {
  const [metric, setMetric] = useState<Metric>("cost");
  const totals = sum(days.map((d) => d.totals));
  return (
    <>
      <MetricTabs totals={totals} previous={previous} metric={metric} onPick={setMetric} />
      <DailyChart days={days} range={range} metric={metric} line={metric === "conversions" ? "clicks" : "conversions"} />
    </>
  );
}

export function DaysTable({ days, range }: { days: Day[]; range: Range }) {
  const rows = fillDays(days, range);
  const all = sum(rows.map((d) => d.totals));
  const columns: Column<Day>[] = [
    {
      id: "day",
      label: "День",
      left: true,
      sort: (d) => d.day,
      cell: (d) => <>{shortDate(d.day)} <span className="cab-muted">{weekday(d.day)}</span></>,
      foot: "Итого",
    },
    ...metricColumns<Day>((d) => d.totals, all),
  ];
  return <SortTable rows={rows} columns={columns} rowKey={(d) => d.day} initial={{ id: "day", desc: true }} />;
}

export function Dim({ loading, children }: { loading: boolean; children: ReactNode }) {
  return <div className={loading ? "cab-stale" : "cab-fresh"} aria-busy={loading}>{children}</div>;
}

export function CampaignScreen({ api, account, id }: { api: Api; account: string; id: string }) {
  const { range } = useReport();
  const { data, error, loading } = useLoad(() => api.campaign(account, id, range), `${account}|${id}|${range.since}|${range.until}`);
  const back = formatHash({ section: "campaigns" }, range);
  if (!data) {
    return (
      <>
        <ReportHead title={error ? "Направление" : "Загружаем…"} crumbs={[{ label: "Реклама", href: back }]} />
        {error ? <p className="cab-alert cab-alert-error" role="alert">{error}</p> : <section className="cab-panel"><Skeleton rows={6} /></section>}
      </>
    );
  }
  const { campaign } = data;
  return (
    <>
      <ReportHead
        title={campaign.name}
        crumbs={[{ label: "Реклама", href: back }]}
        aside={
          <p className="cab-facts">
            <StateMark state={campaign.state} />
            <span className="cab-muted">№ {campaign.campaign_id}</span>
            <span className="cab-muted">лимит {rub(campaign.limit)} в день</span>
          </p>
        }
      />
      <Dim loading={loading}>
        <section className="cab-panel">
          <MetricChart days={data.days} previous={data.previous} range={range} />
        </section>
        <section className="cab-panel">
          <div className="cab-panel-head">
            <h2>Объявления направления</h2>
            <span className="cab-muted">{data.ads.length} шт.</span>
          </div>
          <AdTable ads={data.ads} range={range} />
        </section>
        <section className="cab-panel">
          <h2>По дням</h2>
          <DaysTable days={data.days} range={range} />
        </section>
      </Dim>
    </>
  );
}

export function AdScreen({ api, account, id }: { api: Api; account: string; id: string }) {
  const { range } = useReport();
  const { data, error, loading } = useLoad(() => api.ad(account, id, range), `${account}|${id}|${range.since}|${range.until}`);
  const back = formatHash({ section: "campaigns" }, range);
  if (!data) {
    return (
      <>
        <ReportHead title={error ? "Объявление" : "Загружаем…"} crumbs={[{ label: "Реклама", href: back }]} />
        {error ? <p className="cab-alert cab-alert-error" role="alert">{error}</p> : <section className="cab-panel"><Skeleton rows={6} /></section>}
      </>
    );
  }
  const { ad } = data;
  const site = ad.href.replace(/^https?:\/\//, "").replace(/\/$/, "");
  return (
    <>
      <ReportHead
        title={ad.title}
        crumbs={[
          { label: "Реклама", href: back },
          { label: data.campaign_name ?? `Направление № ${ad.campaign_id}`, href: campaignHref(ad.account, ad.campaign_id, range) },
        ]}
        aside={
          <p className="cab-facts">
            <StateMark state={ad.state} />
            <span className="cab-muted">№ {ad.ad_id}</span>
          </p>
        }
      />
      <Dim loading={loading}>
        <div className="cab-grid2 cab-grid2-ad">
          <section className="cab-panel">
            <MetricChart days={data.days} previous={data.previous} range={range} />
          </section>
          <section className="cab-panel" aria-labelledby="preview">
            <h2 id="preview">Как видят в поиске</h2>
            <div className="cab-ad cab-ad-solo">
              <div className="cab-ad-preview">
                <span className="cab-ad-mark">Реклама</span>
                <span className="cab-ad-title">{ad.title}</span>
                {site && <span className="cab-ad-url">{site}</span>}
                <p className="cab-ad-text">{ad.text}</p>
              </div>
            </div>
            <p className="cab-muted cab-note">Текст — как в Директе сейчас. Снять работающее объявление можно будет, когда кабинет Директа подключат на изменения; каждое такое действие попадёт в историю и отменяется оттуда.</p>
          </section>
        </div>
        <div className="cab-grid2 cab-grid2-even">
          <AudiencePanel audience={data.audience} />
          <BasisPanel basis={data.basis} live />
        </div>
        <section className="cab-panel">
          <h2>По дням</h2>
          <DaysTable days={data.days} range={range} />
        </section>
      </Dim>
    </>
  );
}
