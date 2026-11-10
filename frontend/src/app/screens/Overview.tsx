import { useState } from "react";
import { rub } from "../data";
import { adName, useStoreState } from "../store";
import type { State, Store } from "../store";
import { AiNow } from "./overview/AiNow";
import { Attention } from "./overview/Attention";
import { AutopilotReport } from "./overview/AutopilotReport";
import { Clients } from "./overview/Clients";
import { DayChart } from "./overview/DayChart";
import { Kpis } from "./overview/Kpis";
import "./Overview.css";

function AdsTable({ s, store }: { s: State; store: Store }) {
  const first = s.situation === "first";
  return (
    <section aria-labelledby="ov-ann-title" className="ah-panel">
      <h2 id="ov-ann-title" className="ah-panel-title ov-ann-title">Объявления за 7 дней</h2>
      {s.ads.length === 0 ? (
        <p className="ah-muted" style={{ padding: "16px 0", margin: 0 }}>
          Объявлений пока нет. Заполните услуги в профиле на вкладке «Настройки», чтобы сервис составил объявления.
        </p>
      ) : (
        <div className="ah-table-wrap">
          <table className="ah-table">
            <thead>
              <tr><th scope="col">Объявление</th><th scope="col">Обращения</th><th scope="col">Цена</th><th scope="col">Расход</th></tr>
            </thead>
            <tbody>
              {s.ads.filter((a) => !((a.tag === "заменено" || a.tag === "в архиве") && a.cost === 0 && a.leads === 0)).map((a) => {
                const off = !first && !!s.adsOff[a.id];
                const neutral = first || off;
                const empty = first || a.leads <= 0;
                return (
                  <tr key={a.id} className={off ? "ov-row-off" : undefined}>
                    <td className="ov-anno-cell">
                      <span className="ov-thumb" aria-hidden="true" style={a.photo ? { backgroundImage: `url(${a.photo})` } : undefined} />
                      <button type="button" className="ah-link ov-anno-name" aria-label={`Открыть «${adName(s, a.id)}»`} onClick={() => store.openAdScreen(a.id)}>{adName(s, a.id)}</button>{" "}
                      <span className={`ah-tag ah-tag-${neutral ? "neutral" : a.tagTone}`}>{first ? "собираю данные" : off ? "отключено" : a.tag}</span>
                    </td>
                    <td>{empty ? "—" : a.leads}</td>
                    <td>{empty ? "—" : rub(a.cost / a.leads)}</td>
                    <td>{first || a.cost <= 0 ? "—" : rub(a.cost)}</td>
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

function Feed({ s, store }: { s: State; store: Store }) {
  const [onlyAuto, setOnlyAuto] = useState(false);
  const list = onlyAuto ? s.hist.filter((h) => h.auto) : s.hist;
  return (
    <section aria-labelledby="ov-feed-title" className="ah-panel ov-feed">
      <h2 id="ov-feed-title" className="ah-panel-title">Последние действия</h2>
      <div className="ah-hist-filters" role="group" aria-label="Какие действия показать">
        <button type="button" className="ah-chip" aria-pressed={!onlyAuto} onClick={() => setOnlyAuto(false)}>Все</button>
        <button type="button" className="ah-chip" aria-pressed={onlyAuto} onClick={() => setOnlyAuto(true)}>Автопилот</button>
      </div>
      {onlyAuto && list.length === 0 && <p className="ah-muted">Автопилот пока ничего не менял.</p>}
      <ul>
        {list.slice(0, 3).map((h) => (
          <li key={h.id}><span className="ov-feed-when">{h.when}</span><div>{h.title}</div></li>
        ))}
      </ul>
      <button type="button" className="ah-btn ov-feed-all" onClick={() => store.goView("history")}>Вся история и отмена</button>
    </section>
  );
}

export function Overview({ store }: { store: Store }) {
  const s = useStoreState(store);
  return (
    <div className="ov">
      <Attention s={s} store={store} />
      <AutopilotReport store={store} />
      <Clients s={s} store={store} />
      <Kpis s={s} />
      <div className="ov-row">
        <DayChart s={s} store={store} />
        <AiNow s={s} />
      </div>
      <div className="ov-row">
        <AdsTable s={s} store={store} />
        <Feed s={s} store={store} />
      </div>
    </div>
  );
}
