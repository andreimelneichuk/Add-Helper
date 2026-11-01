import { useCallback, useEffect, useState } from "react";
import type { AdLine, Api, Board, Creative, CreativeReport, Overview } from "../api";
import { count, dateTime, rub } from "../format";
import { AudiencePanel, BasisPanel } from "./AdFacts";
import { Skeleton, useReport } from "./report";
import { adHref, creativeHref, formatHash } from "./route";
import { CREATIVE_NEXT, CREATIVE_STATE, STATE_REASON, WRITE_REASON } from "./words";

type Item = Creative & { campaign_name: string | null };

const PREPARING = ["launch_pending", "ready", "reserve"];
const GONE = ["stopped", "withdrawn"];
// Пока пишутся тексты, обновляем экран: набор готов через минуту-другую.
const POLL_MS = 10_000;

export function CreativeMark({ state }: { state: string }) {
  const word = CREATIVE_STATE[state] ?? { label: state, tone: "warn" as const };
  return <span className={`cab-state cab-state-${word.tone}`}>{word.label}</span>;
}

function Preview({ headline, text }: { headline: string; text: string }) {
  return (
    <div className="cab-ad-preview">
      <span className="cab-ad-mark">Реклама</span>
      <span className="cab-ad-title">{headline}</span>
      <p className="cab-ad-text">{text}</p>
    </div>
  );
}

function campaignOf(item: { campaign_name: string | null; campaign_id: string }): string {
  return item.campaign_name ?? `Направление № ${item.campaign_id}`;
}

function plural(n: number, one: string, few: string, many: string): string {
  const tail = n % 100;
  if (tail >= 11 && tail <= 14) return many;
  if (n % 10 === 1) return one;
  return n % 10 >= 2 && n % 10 <= 4 ? few : many;
}

export function Ads({ api, overview }: { api: Api; overview: Overview | null }) {
  const { range } = useReport();
  const [board, setBoard] = useState<Board | null>(null);
  const [live, setLive] = useState<AdLine[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => api.creatives().then(setBoard, (e: Error) => setMessage(e.message)), [api]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    api.ads(range).then(setLive, () => setLive([]));
  }, [api, range.since, range.until]);

  const writing = (board?.writing.length ?? 0) > 0;
  useEffect(() => {
    if (!writing) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [writing, load]);

  const act = async (id: number, action: "launch" | "withdraw") => {
    setMessage(null);
    try {
      await (action === "launch" ? api.launch(id) : api.withdraw(id));
    } catch (e) {
      setMessage((e as Error).message);
    }
    await load();
  };

  const names = new Map(overview?.campaigns.map((c) => [`${c.account}|${c.campaign_id}`, c.name]) ?? []);
  const running = (live ?? []).filter((ad) => ad.state === "on");
  const items = board?.creatives ?? [];
  const decide = items.filter((c) => c.state === "needs_owner");
  // Сначала то, что скоро начнёт показываться, потом запас.
  const preparing = items
    .filter((c) => PREPARING.includes(c.state))
    .sort((a, b) => PREPARING.indexOf(a.state) - PREPARING.indexOf(b.state));
  const launching = preparing.filter((c) => c.state === "launch_pending").length;
  const gone = items.filter((c) => GONE.includes(c.state));

  return (
    <>
      <div className="cab-head">
        <h1>Объявления</h1>
        <p className="cab-muted cab-period">
          Система сама пишет объявления, ставит их в направления и заменяет те, что устарели. Все цены, адреса и
          обещания берём только из вашего профиля.
        </p>
      </div>
      {message && <p className="cab-alert cab-alert-warn" role="alert">{message}</p>}
      {board === null ? (
        <section className="cab-panel"><Skeleton rows={4} /></section>
      ) : (
        <>
          <p className="cab-now" role="status">
            {summary(running.length, decide.length, launching, preparing.length - launching, board.writing.length)}
          </p>

          {decide.length > 0 && (
            <section className="cab-panel cab-panel-attention" aria-labelledby="decide">
              <div className="cab-panel-head">
                <h2 id="decide">Нужно ваше решение</h2>
                <span className="cab-muted">{decide.length} шт.</span>
              </div>
              <p className="cab-muted">{CREATIVE_NEXT.needs_owner}</p>
              <div className="cab-ads">
                {decide.map((c) => (
                  <article key={c.id} className="cab-ad">
                    <a className="cab-ad-link" href={creativeHref(c.id, null)}>
                      <span className="cab-ad-camp">{campaignOf(c)}</span>
                      <Preview headline={c.headline} text={c.text} />
                    </a>
                    <div className="cab-ad-actions">
                      <button type="button" className="cab-btn cab-btn-primary" onClick={() => act(c.id, "launch")}>Запустить</button>
                      <button type="button" className="cab-btn" onClick={() => act(c.id, "withdraw")}>Не запускать</button>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}

          <section className="cab-panel" aria-labelledby="running">
            <div className="cab-panel-head">
              <h2 id="running">Работают</h2>
              <span className="cab-muted">{running.length} шт.</span>
            </div>
            {live === null ? (
              <Skeleton rows={2} />
            ) : running.length === 0 ? (
              <p className="cab-empty">Сейчас ни одно объявление не показывается.</p>
            ) : (
              <div className="cab-ads">
                {running.map((ad) => (
                  <a key={`${ad.account}|${ad.ad_id}`} className="cab-ad cab-ad-link" href={adHref(ad.account, ad.ad_id, range)}>
                    <span className="cab-ad-camp">{names.get(`${ad.account}|${ad.campaign_id}`) ?? `Направление № ${ad.campaign_id}`}</span>
                    <Preview headline={ad.title} text={ad.text} />
                    <span className="cab-ad-actions cab-muted">
                      {count(ad.totals.clicks)} {plural(ad.totals.clicks, "клик", "клика", "кликов")} · {rub(ad.totals.cost)} за период
                    </span>
                  </a>
                ))}
              </div>
            )}
          </section>

          <section className="cab-panel" aria-labelledby="preparing">
            <div className="cab-panel-head">
              <h2 id="preparing">Готовим</h2>
              <span className="cab-muted">{preparing.length + board.writing.length > 0 ? `${preparing.length} шт.` : ""}</span>
            </div>
            {board.writing.map((w) => (
              <p key={w.id} className="cab-writing">
                <span className="cab-spinner" aria-hidden="true" />
                Пишем {w.wanted} {plural(w.wanted, "объявление", "объявления", "объявлений")} {WRITE_REASON[w.reason] ?? ""} в «{campaignOf(w)}»
              </p>
            ))}
            {preparing.length === 0 && board.writing.length === 0 ? (
              <p className="cab-empty">Все места в направлениях заняты, писать новые сейчас не нужно.</p>
            ) : (
              <CreativeList items={preparing} />
            )}
          </section>

          {gone.length > 0 && (
            <details className="cab-panel cab-gone">
              <summary>Сняты: {gone.length}</summary>
              <CreativeList items={gone} />
            </details>
          )}
        </>
      )}
    </>
  );
}

function CreativeList({ items }: { items: Item[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="cab-clist">
      {items.map((c) => (
        <li key={c.id}>
          <a href={creativeHref(c.id, null)}>
            <span className="cab-clist-text">
              <strong>{c.headline}</strong>
              <span className="cab-muted">{campaignOf(c)}</span>
            </span>
            <CreativeMark state={c.state} />
          </a>
        </li>
      ))}
    </ul>
  );
}

function summary(running: number, decide: number, launching: number, reserve: number, writing: number): string {
  const parts = [`${running} ${plural(running, "объявление работает", "объявления работают", "объявлений работают")}`];
  if (launching) parts.push(`${launching} готовим к запуску`);
  if (reserve) parts.push(`${reserve} ${plural(reserve, "ждёт", "ждут", "ждут")} в запасе`);
  if (writing) parts.push(`пишем новые для ${writing} ${plural(writing, "направления", "направлений", "направлений")}`);
  const now = `Сейчас ${parts.join(", ")}.`;
  if (decide) return `${now} ${decide} ${plural(decide, "ждёт", "ждут", "ждут")} вашего решения.`;
  return `${now} От вас ничего не нужно.`;
}

export function CreativeScreen({ api, id }: { api: Api; id: string }) {
  const [report, setReport] = useState<CreativeReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const back = formatHash({ section: "ads" }, null);

  useEffect(() => {
    api.creative(Number(id)).then(setReport, (e: Error) => setError(e.message));
  }, [api, id]);

  const act = async (action: "launch" | "withdraw") => {
    setBusy(true);
    setError(null);
    try {
      setReport(await (action === "launch" ? api.launch(Number(id)) : api.withdraw(Number(id))));
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  };

  const crumbs = (
    <nav className="cab-crumbs" aria-label="Путь">
      <a href={back}>Объявления</a>
      <span aria-hidden="true"> › </span>
    </nav>
  );
  if (!report) {
    return (
      <div className="cab-head">
        {crumbs}
        <h1>{error ? "Объявление" : "Загружаем…"}</h1>
        {error ? <p className="cab-alert cab-alert-error" role="alert">{error}</p> : <Skeleton rows={4} />}
      </div>
    );
  }
  const { creative } = report;
  const canWithdraw = !GONE.includes(creative.state);
  const last = creative.history[creative.history.length - 1];
  return (
    <>
      <div className="cab-head">
        {crumbs}
        <h1>{creative.headline}</h1>
        <p className="cab-facts">
          <CreativeMark state={creative.state} />
          <span className="cab-muted">{campaignOf({ ...creative, campaign_name: report.campaign_name })}</span>
        </p>
      </div>
      {error && <p className="cab-alert cab-alert-warn" role="alert">{error}</p>}
      <div className="cab-grid2 cab-grid2-ad">
        <section className="cab-panel" aria-labelledby="what">
          <h2 id="what">Что происходит</h2>
          <p>
            <strong>{CREATIVE_STATE[creative.state]?.label ?? creative.state}</strong>
            {last && <span className="cab-muted"> · {dateTime(last.at)}</span>}
          </p>
          <p>{STATE_REASON[creative.state_reason] ?? ""}</p>
          <p>{CREATIVE_NEXT[creative.state] ?? ""}</p>
          {(creative.state === "needs_owner" || canWithdraw) && (
            <div className="cab-actions">
              {creative.state === "needs_owner" && (
                <button type="button" className="cab-btn cab-btn-primary" disabled={busy} onClick={() => act("launch")}>Запустить</button>
              )}
              {canWithdraw && (
                <button type="button" className="cab-btn" disabled={busy} onClick={() => act("withdraw")}>Не запускать</button>
              )}
            </div>
          )}
          {canWithdraw && creative.state !== "needs_owner" && (
            <p className="cab-muted cab-note">Ничего делать не нужно. Если текст вам не нравится, нажмите «Не запускать» — напишем другой.</p>
          )}
        </section>
        <section className="cab-panel" aria-labelledby="preview">
          <h2 id="preview">Как увидят в поиске</h2>
          <div className="cab-ad cab-ad-solo">
            <Preview headline={creative.headline} text={creative.text} />
          </div>
          <p className="cab-muted cab-note">Заголовок {creative.headline.length} из 56 знаков, текст {creative.text.length} из 81.</p>
        </section>
      </div>
      <div className="cab-grid2 cab-grid2-even">
        <AudiencePanel audience={report.audience} />
        <BasisPanel basis={report.basis} live={false} />
      </div>
      <section className="cab-panel" aria-labelledby="steps">
        <h2 id="steps">История</h2>
        <ol className="cab-steps">
          {creative.history.map((h, i) => (
            <li key={i}>
              <span className="cab-muted">{dateTime(h.at)}</span>
              <span>
                <strong>{CREATIVE_STATE[h.state]?.label ?? h.state}.</strong> {STATE_REASON[h.reason] ?? ""}
                {i === 0 && WRITE_REASON[creative.reason] ? ` Писали ${WRITE_REASON[creative.reason]}.` : ""}
              </span>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
