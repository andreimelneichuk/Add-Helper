import { useState } from "react";
import type { Range } from "../../api";
import { rub } from "../data";
import type { Period, PeriodStats } from "../data";
import { adName, statsOf, useStoreState, week } from "../store";
import type { State, Store } from "../store";
import { dayAt, dayNumber, liveApi } from "../live";
import { DirectionReport, OwnPeriod } from "./StatsLive";
import "./Stats.css";

const PERIODS: Period[] = ["week", "month"];
// В данных нет показов и кликов первых дней; расход и обращения считаем по дням.
const FIRST_SHOWS = 210;
const FIRST_CLICKS = 9;

type Row = { name: string; cost: number; leads: number; group: boolean; id?: string };
type Own = { range: Range; stats: PeriodStats };

const price = (cost: number, count: number) => (count > 0 ? rub(cost / count) : "—");

function periodOf(s: State): PeriodStats {
  if (s.situation !== "first") return statsOf(s, s.period);
  const { cost, leads } = week(s);
  return { label: statsOf(s, s.period).label, cost, leads, shows: FIRST_SHOWS, clicks: FIRST_CLICKS, dirs: [] };
}

function rowsOf(s: State, per: PeriodStats): Row[] {
  // Новая цена из профиля меняет название объявления и здесь.
  const shown = (name: string) => {
    const ad = s.ads.find((a) => a.name === name);
    return ad ? adName(s, ad.id) : name;
  };
  return per.dirs.flatMap(([name, leads, cost, ads, dir]) => [
    { name, cost, leads, group: true, id: dir },
    // У настоящих объявлений названия могут совпадать, поэтому у них свой id.
    ...ads.map(([n, l, c, id]) => ({ name: id ? n : shown(n), cost: c, leads: l, group: false, id })),
  ]);
}

const idOf = (s: State, name: string) => s.ads.find((a) => adName(s, a.id) === name)?.id ?? "";

/** Дни выбранного готового периода: отчёт направления просит их у сервиса. */
function rangeOf(s: State): Range | null {
  if (!s.live) return null;
  const { today } = s.live;
  return { since: s.period === "week" ? dayAt(dayNumber(today) - 6) : `${today.slice(0, 8)}01`, until: today };
}

export function Stats({ store }: { store: Store }) {
  const s = useStoreState(store);
  const api = liveApi(store);
  const [own, setOwn] = useState<Own | null>(null);
  const [picking, setPicking] = useState(false);
  const [dir, setDir] = useState<string | null>(null);
  const per = own?.stats ?? periodOf(s);
  const range = own?.range ?? rangeOf(s);
  if (api && dir && range) return <div className="stats"><DirectionReport api={api} id={dir} range={range} label={per.label} back={() => setDir(null)} openAd={store.openAdScreen} /></div>;
  const rows = rowsOf(s, per);
  const first = s.situation === "first";
  const ctr = per.shows > 0 ? (per.clicks * 100 / per.shows).toFixed(1).replace(".", ",") + "%" : "—";
  const nums: [string, string | number][] = [
    ["Обращения", per.leads],
    ["Цена обращения", price(per.cost, per.leads)],
    ["Показы", per.shows.toLocaleString("ru-RU")],
    ["Клики", per.clicks.toLocaleString("ru-RU")],
    ["CTR, доля нажавших", ctr],
    ["Цена клика", price(per.cost, per.clicks)],
  ];

  return (
    <div className="stats">
      <div className="ah-chips" role="group" aria-label="Период">
        {PERIODS.map((id) => (
          <button key={id} type="button" className="ah-chip" aria-pressed={!own && !picking && s.period === id} onClick={() => { setOwn(null); setPicking(false); store.set({ period: id }); }}>{statsOf(s, id).label}</button>
        ))}
        {api && <button type="button" className="ah-chip" aria-pressed={!!own || picking} onClick={() => setPicking(true)}>{own && !picking ? own.stats.label : "Свой период"}</button>}
      </div>
      {api && picking && <OwnPeriod api={api} today={s.live!.today} picked={(r, stats) => { setOwn({ range: r, stats }); setPicking(false); }} />}
      <div className="stats-grid">
        <section className="ah-panel">
          <h2 className="ah-panel-title">Что приносит обращения</h2>
          <p className="stats-note">{s.live ? `${own ? `${own.stats.label}. ` : ""}Данные на ${Number(s.live.today.slice(8))} ${s.live.monthOf}. Яндекс уточняет их до трёх дней. Нажмите на направление, чтобы открыть его отчёт.` : first ? "Пока мало данных: разбивка появится, когда обращений станет больше." : "Данные на 14 ноября. В прототипе разбивка одна для всех ситуаций."}</p>
          {rows.length > 0 && (
            <div className="ah-table-wrap" role="region" aria-label="Разбивка по услугам и объявлениям" tabIndex={0}>
              <table className="ah-table">
                <thead>
                  <tr><th scope="col">Что</th><th scope="col">Расход</th><th scope="col">Обращения</th><th scope="col">Цена</th></tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={`${i}-${r.name}`} className={r.group ? "stats-row-group" : "stats-row-ad"}>
                      <td>{r.group ? (api && r.id ? <button type="button" className="ah-link" aria-label={`Отчёт по направлению «${r.name}»`} onClick={() => setDir(r.id!)}>{r.name}</button> : r.name) : <button type="button" className="ah-link" aria-label={`Открыть «${r.name}»`} onClick={() => store.openAdScreen(r.id ?? idOf(s, r.name))}>{r.name}</button>}</td><td>{rub(r.cost)}</td><td>{r.leads}</td><td>{price(r.cost, r.leads)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <div className="stats-side">
          <section className="ah-panel">
            <h2 className="ah-panel-title">Все цифры</h2>
            <dl className="stats-nums">
              {nums.map(([label, value]) => (
                <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
              ))}
            </dl>
          </section>
          <section className="ah-panel">
            <h2 className="ah-panel-title">Сверка с Яндексом</h2>
            {s.live ? (
              <p className="stats-note stats-check-note">{s.live.gap === null ? "Сверки со счётом ещё не было." : `Разница между счётом и статистикой ${rub(s.live.gap)}. Яндекс уточняет данные до трёх дней.`}</p>
            ) : (
              <>
                <div className="stats-check"><span>У нас</span><span>{rub(per.cost)}</span></div>
                <div className="stats-check"><span>В кабинете</span><span>{rub(per.cost)}</span></div>
                <p className="stats-note stats-check-note">Разница 0 ₽. Яндекс уточняет данные до трёх дней.</p>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
