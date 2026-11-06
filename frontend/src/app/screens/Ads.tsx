import { useState } from "react";
import type { Ad } from "../data";
import { adName, adShown, adStat, useStoreState } from "../store";
import type { State, Store } from "../store";
import { liveApi } from "../live";
import type { LiveStore } from "../live";
import { AdArt, AdDetail, AdMaker } from "./AdDetail";
import { Decisions } from "./Decisions";
import { GenerateAdsModal } from "./ads/GenerateAdsModal";
import "./Ads.css";

function Card({ store, s, ad }: { store: Store; s: State; ad: Ad }) {
  const off = !!s.adsOff[ad.id];
  const name = adName(s, ad.id);
  const { hook, title } = adShown(s, ad);
  const action = off ? "Включить" : "Отключить";
  return (
    <article className={off ? "ads-card ads-card-off" : "ads-card"}>
      <div className="ads-thumb">
        <AdArt ad={ad} hook={hook} />
        {!off && s.situation !== "first" && <span className={`ah-tag ah-tag-${ad.tagTone} ads-badge`}>{ad.tag}</span>}
      </div>
      <div className="ads-body">
        <div className="ads-title">{title}</div>
        <div className="ads-text">{ad.text}</div>
        <div className="ads-stat">{adStat(s, ad)}</div>
        <div className="ads-facts">{ad.facts}</div>
        <div className="ads-foot">
          <button type="button" className="ah-btn" aria-label={`Открыть «${name}»`} onClick={() => store.openAdScreen(ad.id)}>Открыть</button>
          <button type="button" className="ah-btn" aria-label={`${action} «${name}»`} onClick={() => (off ? store.run({ action: "ad", value: ad.id }) : store.ask({ action: "ad", value: ad.id }))}>{action}</button>
        </div>
      </div>
    </article>
  );
}

type Filter = "all" | "on" | "off";

export function Ads({ store }: { store: Store }) {
  const s = useStoreState(store);
  const [filter, setFilter] = useState<Filter>("all");
  const [genModalOpen, setGenModalOpen] = useState(false);
  if (s.openAd === "new") return <AdMaker store={store} />;
  if (s.openAd && s.ads.some((a) => a.id === s.openAd)) return <AdDetail store={store} id={s.openAd} />;
  const groups: Record<Filter, Ad[]> = { all: s.ads, on: s.ads.filter((a) => !s.adsOff[a.id]), off: s.ads.filter((a) => s.adsOff[a.id]) };
  const filters: [Filter, string][] = [["all", "Все"], ["on", "Работают"], ["off", "Отключены"]];
  const api = liveApi(store);

  const list = (
    <>
      <div className="ads-list-head">
        <h2 id="ads-list-title" className="ah-panel-title">Объявления</h2>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <button type="button" className="ah-btn ah-btn-primary" onClick={() => setGenModalOpen(true)}>
            Сгенерировать объявления
          </button>
          {!s.live && <button type="button" className="ah-btn" onClick={() => store.openAdScreen("new")}>Новое объявление</button>}
        </div>
      </div>
      <div className="ah-chips" role="group" aria-label="Какие показать">
        {filters.map(([id, label]) => (
          <button key={id} type="button" className="ah-chip" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label} {groups[id].length}</button>
        ))}
      </div>
      {groups[filter].length === 0 && <p className="ah-muted">{filter === "off" ? "Отключённых объявлений нет." : "Сейчас ни одно объявление не показывается."}</p>}
      <div className="ads-cards">
        {groups[filter].map((ad) => <Card key={ad.id} store={store} s={s} ad={ad} />)}
      </div>
      <GenerateAdsModal isOpen={genModalOpen} onClose={() => setGenModalOpen(false)} store={store} />
    </>
  );
  return (
    <section className="ads" aria-labelledby="ads-list-title">
      {api ? <Decisions api={api} store={store as LiveStore}>{list}</Decisions> : list}
    </section>
  );
}
