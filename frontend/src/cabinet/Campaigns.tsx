import { useState } from "react";
import type { AdLine, Api, Campaign, Overview, Range, Totals } from "../api";
import { count, percent, rub, rubExact } from "../format";
import { conversion, cpa, cpc, ctr } from "./Metrics";
import { sum } from "./period";
import { ReportHead, Skeleton, useLoad, useReport } from "./report";
import { adHref, campaignHref } from "./route";
import { SortTable, type Column } from "./Table";
import { CAMPAIGN_STATE } from "./words";

type Filter = "all" | "on" | "off";
type Ad = Omit<AdLine, "ratios">;
type View = "campaigns" | "ads";

const go = (href: string) => {
  window.location.hash = href;
};

export function Campaigns({ overview, api }: { overview: Overview; api: Api }) {
  const [view, setView] = useState<View>("campaigns");
  const [filter, setFilter] = useState<Filter>("all");
  const { range } = useReport();
  const ads = useLoad(() => (view === "ads" ? api.ads(range) : Promise.resolve(null)), `${view}|${range.since}|${range.until}`);

  const lines: { state: string }[] = view === "campaigns" ? overview.campaigns : (ads.data ?? []);
  const on = lines.filter((c) => c.state === "on").length;
  const keep = (state: string) => filter === "all" || (filter === "on") === (state === "on");
  const tabs: { id: Filter; label: string; size: number }[] = [
    { id: "all", label: "Все", size: lines.length },
    { id: "on", label: "Идут показы", size: on },
    { id: "off", label: "Остановлены", size: lines.length - on },
  ];
  const names = Object.fromEntries(overview.campaigns.map((c) => [`${c.account}:${c.campaign_id}`, c.name]));

  return (
    <>
      <ReportHead title="Реклама" />
      <section className="cab-panel">
        <div className="cab-panel-head">
          <div className="cab-segment" role="tablist" aria-label="Что показать">
            {(["campaigns", "ads"] as View[]).map((v) => (
              <button key={v} type="button" role="tab" aria-selected={view === v} className={view === v ? "cab-seg cab-seg-on" : "cab-seg"} onClick={() => setView(v)}>
                {v === "campaigns" ? "Направления" : "Объявления"}
              </button>
            ))}
          </div>
          <div className="cab-tabs cab-tabs-inline" role="tablist" aria-label="Фильтр по состоянию">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={filter === tab.id}
                className={filter === tab.id ? "cab-tab cab-tab-active" : "cab-tab"}
                onClick={() => setFilter(tab.id)}
              >
                {tab.label} <span className="cab-muted">{tab.size}</span>
              </button>
            ))}
          </div>
        </div>
        {view === "campaigns" ? (
          <CampaignTable campaigns={overview.campaigns.filter((c) => keep(c.state))} range={range} />
        ) : ads.data === null ? (
          ads.error ? <p className="cab-bad">{ads.error}</p> : <Skeleton rows={4} />
        ) : (
          <AdTable ads={ads.data.filter((a) => keep(a.state))} range={range} campaignName={(a) => names[`${a.account}:${a.campaign_id}`]} />
        )}
        <p className="cab-muted cab-note">
          Нажмите на строку, чтобы открыть подробный отчёт. Дневной лимит ставит защита бюджета: сумма лимитов не выходит за бюджет месяца, даже если сервис остановится.
          Обращения — цели кода статистики на сайте.
        </p>
      </section>
    </>
  );
}

export function StateMark({ state }: { state: string }) {
  const word = CAMPAIGN_STATE[state] ?? { label: state, tone: "warn" as const };
  return <span className={`cab-state cab-state-${word.tone}`}>{word.label}</span>;
}

// Общие столбцы показателей: одинаковы для направлений, объявлений и дней.
export function metricColumns<Row>(totals: (r: Row) => Totals, all: Totals, compact = false): Column<Row>[] {
  const full: (Column<Row> & { short?: boolean })[] = [
    { id: "impressions", label: "Показы", sort: (r) => totals(r).impressions, cell: (r) => count(totals(r).impressions), foot: count(all.impressions) },
    { id: "clicks", label: "Клики", short: true, sort: (r) => totals(r).clicks, cell: (r) => count(totals(r).clicks), foot: count(all.clicks) },
    { id: "ctr", label: "CTR", sort: (r) => ctr(totals(r)), cell: (r) => percent(ctr(totals(r))), foot: percent(ctr(all)) },
    { id: "cpc", label: "Цена клика", sort: (r) => cpc(totals(r)), cell: (r) => rubExact(cpc(totals(r))), foot: rubExact(cpc(all)) },
    { id: "cost", label: "Расход", short: true, sort: (r) => totals(r).cost, cell: (r) => rub(totals(r).cost), foot: rub(all.cost) },
    { id: "conversions", label: "Обращения", short: true, sort: (r) => totals(r).conversions, cell: (r) => count(totals(r).conversions), foot: count(all.conversions) },
    { id: "conversion", label: "Конверсия", sort: (r) => conversion(totals(r)), cell: (r) => percent(conversion(totals(r))), foot: percent(conversion(all)) },
    {
      id: "cpa",
      label: "Цена обращения",
      short: true,
      sort: (r) => cpa(totals(r)),
      cell: (r) => (cpa(totals(r)) === null ? <span className="cab-muted">нет обращений</span> : rub(cpa(totals(r)))),
      foot: rub(cpa(all)),
    },
  ];
  return compact ? full.filter((c) => c.short) : full;
}

export function CampaignTable({ campaigns, range, compact }: { campaigns: Campaign[]; range: Range; compact?: boolean }) {
  const all = sum(campaigns.map((c) => c.totals));
  const limits = campaigns.filter((c) => c.state === "on").reduce((s, c) => s + (c.limit ?? 0), 0);
  const columns: Column<Campaign>[] = [
    {
      id: "name",
      label: "Направление",
      left: true,
      sort: (c) => c.name,
      cell: (c) => (
        <>
          <a className="cab-name" href={campaignHref(c.account, c.campaign_id, range)} onClick={(e) => e.stopPropagation()}>{c.name}</a>
          <span className="cab-muted cab-id">№ {c.campaign_id}</span>
        </>
      ),
      foot: "Итого",
    },
    { id: "state", label: "Статус", left: true, sort: (c) => c.state, cell: (c) => <StateMark state={c.state} />, foot: "" },
    ...(compact
      ? []
      : [{ id: "limit", label: "Лимит в день", title: "Сумма лимитов работающих направлений в итоге", sort: (c: Campaign) => c.limit, cell: (c: Campaign) => rub(c.limit), foot: rub(limits) }]),
    ...metricColumns<Campaign>((c) => c.totals, all, compact),
  ];
  return (
    <SortTable
      rows={campaigns}
      columns={columns}
      rowKey={(c) => `${c.account}:${c.campaign_id}`}
      onRow={(c) => go(campaignHref(c.account, c.campaign_id, range))}
      compact={compact}
      empty="Направлений нет."
    />
  );
}

export function AdTable({
  ads,
  range,
  campaignName,
}: {
  ads: Ad[];
  range: Range;
  campaignName?: (ad: Ad) => string | undefined;
}) {
  type Row = Ad;
  const all = sum(ads.map((a) => a.totals));
  const columns: Column<Row>[] = [
    {
      id: "title",
      label: "Объявление",
      left: true,
      sort: (a) => a.title,
      cell: (a) => (
        <>
          <a className="cab-name" href={adHref(a.account, a.ad_id, range)} onClick={(e) => e.stopPropagation()}>{a.title}</a>
          <span className="cab-muted cab-ad-line">{a.text}</span>
          <span className="cab-muted cab-id">№ {a.ad_id}{campaignName?.(a) ? ` · ${campaignName(a)}` : ""}</span>
        </>
      ),
      foot: "Итого",
    },
    { id: "state", label: "Статус", left: true, sort: (a) => a.state, cell: (a) => <StateMark state={a.state} />, foot: "" },
    ...metricColumns<Row>((a) => a.totals, all),
  ];
  return (
    <SortTable
      rows={ads}
      columns={columns}
      rowKey={(a) => `${a.account}:${a.ad_id}`}
      onRow={(a) => go(adHref(a.account, a.ad_id, range))}
      empty="Объявлений нет: их загрузит адаптер Директа после подключения кабинета."
    />
  );
}
