import { useState } from "react";
import type { Ad } from "../data";
import { adName, adShown, adStat, useStore } from "../store";
import type { State, Store } from "../store";
import { liveApi } from "../live";
import type { LiveStore } from "../live";
import { AdArt, AdDetail, AdMaker } from "./AdDetail";
import { Decisions } from "./Decisions";
import { GenerateAdsModal } from "./ads/GenerateAdsModal";
import { LaunchModal } from "./ads/LaunchModal";
import "./Ads.css";

type AdsView = Pick<State, "price" | "ads" | "adsOff" | "situation" | "live" | "openAd">;

function Card({ store, s, ad }: { store: Store; s: AdsView; ad: Ad }) {
  const isArchived = ad.tag === "в архиве" || ad.tag === "заменено";
  const off = !isArchived && (!!s.adsOff[ad.id] || ad.tag === "отключено");
  const name = adName(s, ad.id);
  const { hook, title } = adShown(s, ad);
  const action = off ? "Включить" : "Отключить";
  return (
    <article className={off || isArchived ? "ann-card ann-card-off" : "ann-card"}>
      {ad.photo ? (
        <div className="ann-thumb">
          <AdArt ad={ad} hook={hook} />
          {!off && s.situation !== "first" && <span className={`ah-tag ah-tag-${ad.tagTone} ann-badge`}>{ad.tag}</span>}
        </div>
      ) : (
        <div className="ann-text-preview">
          {!off && s.situation !== "first" && <span className={`ah-tag ah-tag-${ad.tagTone}`}>{ad.tag}</span>}
          <div className="ann-title">{title}</div>
          <div className="ann-text">{ad.text}</div>
        </div>
      )}
      <div className="ann-body">
        {ad.photo && <><div className="ann-title">{title}</div><div className="ann-text">{ad.text}</div></>}
        <div className="ann-stat">{adStat(s, ad)}</div>
        <div className="ann-facts">{ad.facts}</div>
        <div className="ann-foot">
          <button type="button" className="ah-btn" aria-label={`Открыть «${name}»`} onClick={() => store.openAdScreen(ad.id)}>Открыть</button>
          {ad.tag === "готово к запуску" && s.live?.hasCampaigns && (
            <>
              <button type="button" className="ah-btn ah-btn-primary" aria-label={`Опубликовать «${name}» по услугам`} onClick={() => store.ask({ action: "adPublish", value: ad.id, grouping: "services" })}>По услугам</button>
              <button type="button" className="ah-btn" aria-label={`Опубликовать «${name}» общей темой`} onClick={() => store.ask({ action: "adPublish", value: ad.id, grouping: "general" })}>Общая тема</button>
            </>
          )}
          {isArchived ? (
            <button type="button" className="ah-btn" aria-label={`Вернуть «${name}» из архива`} onClick={() => store.ask({ action: "adRestore", value: ad.id })}>Восстановить</button>
          ) : (
            <button type="button" className="ah-btn" aria-label={`${action} «${name}»`} onClick={() => (off ? store.run({ action: "ad", value: ad.id }) : store.ask({ action: "ad", value: ad.id }))}>{action}</button>
          )}
        </div>
      </div>
    </article>
  );
}

type Filter = "all" | "on" | "ready" | "off" | "archived";

export function Ads({ store }: { store: Store }) {
  const s = useStore(store, (x): AdsView => ({ price: x.price, ads: x.ads, adsOff: x.adsOff, situation: x.situation, live: x.live, openAd: x.openAd }));
  const [filter, setFilter] = useState<Filter>("all");
  const [genModalOpen, setGenModalOpen] = useState(false);
  const [launchModalOpen, setLaunchModalOpen] = useState(false);
  if (s.openAd === "new") return <AdMaker store={store} />;
  if (s.openAd && s.ads.some((a) => a.id === s.openAd)) return <AdDetail store={store} id={s.openAd} />;

  const isPrelaunch = s.live ? !s.live.hasCampaigns : s.situation === "first";
  const isArchived = (a: Ad) => a.tag === "в архиве" || a.tag === "заменено";
  const isOff = (a: Ad) => !isArchived(a) && (!!s.adsOff[a.id] || a.tag === "отключено");
  const isReady = (a: Ad) => !isArchived(a) && !isOff(a) && a.tag === "готово к запуску";
  const isOn = (a: Ad) => !isArchived(a) && !isOff(a) && (isPrelaunch ? isReady(a) : a.tag === "работает");

  const groups: Record<Filter, Ad[]> = {
    all: s.ads.filter((a) => !isArchived(a)),
    on: s.ads.filter(isOn),
    ready: s.ads.filter(isReady),
    off: s.ads.filter(isOff),
    archived: s.ads.filter(isArchived),
  };
  const filters: [Filter, string][] = [
    ["all", "Все"],
    ["on", isPrelaunch ? "К запуску" : "Работают"],
    ...(!isPrelaunch ? [["ready", "К запуску"] as [Filter, string]] : []),
    ["off", "Отключены"],
    ["archived", "В архиве"],
  ];
  const api = liveApi(store);

  const list = (
    <>
      <div className="ann-list-head">
        <h2 id="ann-list-title" className="ah-panel-title">Объявления</h2>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
          {isPrelaunch && (
            <button
              type="button"
              className="ah-btn ah-btn-primary"
              onClick={() => setLaunchModalOpen(true)}
            >
              {s.live && !s.live.account ? "Подключить Директ и запустить" : "Запустить рекламу"}
            </button>
          )}
          <button type="button" className="ah-btn" onClick={() => setGenModalOpen(true)}>
            Сгенерировать объявления
          </button>
          <button type="button" className="ah-btn" onClick={() => store.openAdScreen("new")}>
            Новое объявление
          </button>
        </div>
      </div>
      <div className="ah-chips" role="group" aria-label="Какие показать">
        {filters.map(([id, label]) => (
          <button key={id} type="button" className="ah-chip" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label} {groups[id].length}</button>
        ))}
      </div>
      {groups[filter].length === 0 && (
        <p className="ah-muted">
          {filter === "off"
            ? "Отключённых объявлений нет."
            : filter === "archived"
            ? "В архиве пока ничего нет."
            : isPrelaunch
            ? "Нет объявлений, готовых к запуску."
            : "Сейчас ни одно объявление не показывается."}
        </p>
      )}
      <div className="ann-cards">
        {groups[filter].map((ad) => <Card key={ad.id} store={store} s={s} ad={ad} />)}
      </div>
      <GenerateAdsModal isOpen={genModalOpen} onClose={() => setGenModalOpen(false)} store={store} />
      <LaunchModal isOpen={launchModalOpen} onClose={() => setLaunchModalOpen(false)} store={store} />
    </>
  );
  return (
    <section className="ann" aria-labelledby="ann-list-title">
      {api ? <Decisions api={api} store={store as LiveStore}>{list}</Decisions> : list}
    </section>
  );
}
