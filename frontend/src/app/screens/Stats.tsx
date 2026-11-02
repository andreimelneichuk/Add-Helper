import { rub, STATS } from "../data";
import type { Period, PeriodStats } from "../data";
import { adName, useStoreState, week } from "../store";
import type { State, Store } from "../store";
import "./Stats.css";

const PERIODS = Object.keys(STATS) as Period[];
// В данных нет показов и кликов первых дней; расход и обращения считаем по дням.
const FIRST_SHOWS = 210;
const FIRST_CLICKS = 9;

type Row = { name: string; cost: number; leads: number; group: boolean };

const price = (cost: number, count: number) => (count > 0 ? rub(cost / count) : "—");

function periodOf(s: State): PeriodStats {
  if (s.situation !== "first") return STATS[s.period];
  const { cost, leads } = week(s);
  return { label: STATS[s.period].label, cost, leads, shows: FIRST_SHOWS, clicks: FIRST_CLICKS, dirs: [] };
}

function rowsOf(s: State, per: PeriodStats): Row[] {
  // Новая цена из профиля меняет название объявления и здесь.
  const shown = (name: string) => {
    const ad = s.ads.find((a) => a.name === name);
    return ad ? adName(s, ad.id) : name;
  };
  return per.dirs.flatMap(([name, leads, cost, ads]) => [
    { name, cost, leads, group: true },
    ...ads.map(([n, l, c]) => ({ name: shown(n), cost: c, leads: l, group: false })),
  ]);
}

const idOf = (s: State, name: string) => s.ads.find((a) => adName(s, a.id) === name)?.id ?? "";

export function Stats({ store }: { store: Store }) {
  const s = useStoreState(store);
  const per = periodOf(s);
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
          <button key={id} type="button" className="ah-chip" aria-pressed={s.period === id} onClick={() => store.set({ period: id })}>{STATS[id].label}</button>
        ))}
      </div>
      <div className="stats-grid">
        <section className="ah-panel">
          <h2 className="ah-panel-title">Что приносит обращения</h2>
          <p className="stats-note">{first ? "Пока мало данных: разбивка появится, когда обращений станет больше." : "Данные на 14 ноября. В прототипе разбивка одна для всех ситуаций."}</p>
          {rows.length > 0 && (
            <div className="ah-table-wrap" role="region" aria-label="Разбивка по услугам и объявлениям" tabIndex={0}>
              <table className="ah-table">
                <thead>
                  <tr><th scope="col">Что</th><th scope="col">Расход</th><th scope="col">Обращения</th><th scope="col">Цена</th></tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.name} className={r.group ? "stats-row-group" : "stats-row-ad"}>
                      <td>{r.group ? r.name : <button type="button" className="ah-link" aria-label={`Открыть «${r.name}»`} onClick={() => store.openAdScreen(idOf(s, r.name))}>{r.name}</button>}</td><td>{rub(r.cost)}</td><td>{r.leads}</td><td>{price(r.cost, r.leads)}</td>
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
            <div className="stats-check"><span>У нас</span><span>{rub(per.cost)}</span></div>
            <div className="stats-check"><span>В кабинете</span><span>{rub(per.cost)}</span></div>
            <p className="stats-note stats-check-note">Разница 0 ₽. Яндекс уточняет данные до трёх дней.</p>
          </section>
        </div>
      </div>
    </div>
  );
}
