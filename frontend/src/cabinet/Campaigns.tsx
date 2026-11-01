import { useState } from "react";
import type { Campaign, Overview } from "../api";
import { count, percent, rub, rubExact } from "../format";
import { PeriodNote } from "./Cabinet";
import { CAMPAIGN_STATE } from "./words";

type Filter = "all" | "on" | "off";

export function Campaigns({ overview }: { overview: Overview }) {
  const [filter, setFilter] = useState<Filter>("all");
  const on = overview.campaigns.filter((c) => c.state === "on");
  const shown = filter === "all" ? overview.campaigns : filter === "on" ? on : overview.campaigns.filter((c) => c.state !== "on");
  const tabs: { id: Filter; label: string; size: number }[] = [
    { id: "all", label: "Все", size: overview.campaigns.length },
    { id: "on", label: "Идут показы", size: on.length },
    { id: "off", label: "Остановлены", size: overview.campaigns.length - on.length },
  ];
  return (
    <>
      <div className="cab-head">
        <h1>Реклама</h1>
        <PeriodNote overview={overview} />
      </div>
      <section className="cab-panel">
        <div className="cab-tabs" role="tablist" aria-label="Фильтр направлений">
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
        <CampaignTable campaigns={shown} />
        <p className="cab-muted cab-note">
          Дневной лимит ставит защита бюджета: сумма лимитов не выходит за бюджет месяца, даже если сервис остановится.
          Обращения — цели кода статистики на сайте.
        </p>
      </section>
    </>
  );
}

export function CampaignTable({ campaigns, compact }: { campaigns: Campaign[]; compact?: boolean }) {
  if (campaigns.length === 0) return <p className="cab-empty">Направлений нет.</p>;
  const total = campaigns.reduce(
    (acc, c) => ({
      impressions: acc.impressions + c.totals.impressions,
      clicks: acc.clicks + c.totals.clicks,
      cost: acc.cost + c.totals.cost,
      conversions: acc.conversions + c.totals.conversions,
    }),
    { impressions: 0, clicks: 0, cost: 0, conversions: 0 },
  );
  const limits = campaigns.filter((c) => c.state === "on").reduce((sum, c) => sum + (c.limit ?? 0), 0);
  const ctr = total.impressions ? Math.floor((total.clicks * 10_000) / total.impressions) : null;
  const conv = total.clicks ? Math.floor((total.conversions * 10_000) / total.clicks) : null;
  return (
    <div className="cab-table-wrap">
      <table className={compact ? "cab-table cab-table-compact" : "cab-table"}>
        <thead>
          <tr>
            <th className="cab-left">Направление</th>
            <th className="cab-left">Статус</th>
            {!compact && <th>Лимит в день</th>}
            {!compact && <th>Показы</th>}
            <th>Клики</th>
            {!compact && <th>CTR</th>}
            {!compact && <th>Цена клика</th>}
            <th>Расход</th>
            <th>Обращения</th>
            {!compact && <th>Конверсия</th>}
            <th>Цена обращения</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((c) => {
            const state = CAMPAIGN_STATE[c.state] ?? { label: c.state, tone: "warn" as const };
            return (
              <tr key={`${c.account}:${c.campaign_id}`}>
                <td className="cab-left">
                  <span className="cab-name">{c.name}</span>
                  <span className="cab-muted cab-id">№ {c.campaign_id}</span>
                </td>
                <td className="cab-left"><span className={`cab-state cab-state-${state.tone}`}>{state.label}</span></td>
                {!compact && <td>{rub(c.limit)}</td>}
                {!compact && <td>{count(c.totals.impressions)}</td>}
                <td>{count(c.totals.clicks)}</td>
                {!compact && <td>{percent(c.ratios.ctr_bp)}</td>}
                {!compact && <td>{rubExact(c.ratios.cpc)}</td>}
                <td>{rub(c.totals.cost)}</td>
                <td>{count(c.totals.conversions)}</td>
                {!compact && <td>{percent(c.ratios.conversion_bp)}</td>}
                <td>{c.ratios.cpa === null ? <span className="cab-muted">нет обращений</span> : rub(c.ratios.cpa)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <th className="cab-left">Итого</th>
            <th />
            {!compact && <th title="Сумма лимитов работающих направлений">{rub(limits)}</th>}
            {!compact && <th>{count(total.impressions)}</th>}
            <th>{count(total.clicks)}</th>
            {!compact && <th>{percent(ctr)}</th>}
            {!compact && <th>{rubExact(total.clicks ? Math.floor(total.cost / total.clicks) : null)}</th>}
            <th>{rub(total.cost)}</th>
            <th>{count(total.conversions)}</th>
            {!compact && <th>{percent(conv)}</th>}
            <th>{rub(total.conversions ? Math.floor(total.cost / total.conversions) : null)}</th>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
