import { useCallback, useEffect, useRef, useState } from "react";
import type { Ad } from "../data";
import { adName, adShown, adStat, useStore } from "../store";
import type { State, Store } from "../store";
import { liveApi } from "../live";
import { LiveStore } from "../live";
import { AdArt, AdDetail, AdMaker } from "./AdDetail";
import { Decisions } from "./Decisions";
import { GenerateAdsModal } from "./ads/GenerateAdsModal";
import { LaunchModal } from "./ads/LaunchModal";
import "./Ads.css";

type AdsView = Pick<State, "price" | "ads" | "adsOff" | "situation" | "live" | "openAd" | "working">;

function Card({ store, s, ad }: { store: Store; s: AdsView; ad: Ad }) {
  const isArchived = ad.tag === "в архиве" || ad.tag === "заменено";
  const off = !isArchived && (!!s.adsOff[ad.id] || ad.tag === "отключено");
  const name = adName(s, ad.id);
  const { hook, title } = adShown(s, ad);
  const status = isArchived ? ad.tag : off ? "Отключено" : ad.tag;
  const working = s.working?.action === "ad" && s.working.value === ad.id ? (off ? "Включаем…" : "Отключаем…")
    : s.working?.action === "adPublish" && s.working.value === ad.id ? "Публикуем…"
      : s.working?.action === "adArchive" && s.working.value === ad.id ? "Архивируем…"
        : s.working?.action === "adRestore" && s.working.value === ad.id ? "Восстанавливаем…"
          : s.working?.action === "adSave" && s.working.value.id === ad.id ? "Сохраняем…" : null;
  return (
    <article
      role="button"
      tabIndex={0}
      className={`ann-card${off || isArchived ? " ann-card-off" : ""}`}
      aria-busy={!!working}
      aria-label={`Открыть объявление «${name}»`}
      onClick={() => store.openAdScreen(ad.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          store.openAdScreen(ad.id);
        }
      }}
    >
      {ad.photo ? (
        <div className="ann-thumb">
          <AdArt ad={ad} hook={hook} />
          <span className={`ah-tag ah-tag-${off || working ? "neutral" : ad.tagTone} ann-badge`}>{working ?? status}</span>
        </div>
      ) : (
        <div className="ann-text-preview">
          <span className={`ah-tag ah-tag-${off || working ? "neutral" : ad.tagTone}`}>{working ?? status}</span>
          <div className="ann-title">{title}</div>
          <div className="ann-text">{ad.text}</div>
        </div>
      )}
      <div className="ann-body">
        {ad.photo && <><div className="ann-title">{title}</div><div className="ann-text">{ad.text}</div></>}
        <div className="ann-stat"><span>Результат</span><strong>{adStat(s, ad)}</strong></div>
        <div className="ann-foot">
          <span>Открыть объявление</span>
          <span className="ann-open-icon" aria-hidden="true">↗</span>
        </div>
      </div>
    </article>
  );
}

type Filter = "all" | "on" | "moderation" | "ready" | "off" | "archived";

export function Ads({ store }: { store: Store }) {
  const s = useStore(store, (x): AdsView => ({ price: x.price, ads: x.ads, adsOff: x.adsOff, situation: x.situation, live: x.live, openAd: x.openAd, working: x.working }));
  const [filter, setFilter] = useState<Filter>("all");
  const [genModalOpen, setGenModalOpen] = useState(false);
  const [launchModalOpen, setLaunchModalOpen] = useState(false);
  const liveStore = store instanceof LiveStore ? store : null;
  const refreshingAds = useRef(false);
  const refreshAds = useCallback(async () => {
    if (!liveStore || refreshingAds.current) return;
    refreshingAds.current = true;
    try {
      await liveStore.refreshAds();
    } catch { /* Сохраняем последний известный список, если сеть недоступна. */ }
    finally { refreshingAds.current = false; }
  }, [liveStore]);
  useEffect(() => {
    if (!liveStore) return;
    void refreshAds();
    const timer = window.setInterval(() => void refreshAds(), 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshAds();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [liveStore, refreshAds]);
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
    moderation: s.ads.filter((a) => !isArchived(a) && a.tag === "на проверке Яндекса"),
    ready: s.ads.filter(isReady),
    off: s.ads.filter(isOff),
    archived: s.ads.filter(isArchived),
  };
  const filters: [Filter, string][] = [
    ["all", "Все"],
    ["on", isPrelaunch ? "К запуску" : "Работают"],
    ["moderation", "На проверке Яндекса"],
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
          {/* В кабинете автор объявлений один: генератор по профилю; экран с заглушкой там не нужен. */}
          {!s.live && (
            <button type="button" className="ah-btn" onClick={() => store.openAdScreen("new")}>
              Новое объявление
            </button>
          )}
        </div>
      </div>
      <p className="ann-list-lead">Выберите объявление, чтобы посмотреть его и управлять им.</p>
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
            : filter === "moderation"
            ? "Объявлений на проверке Яндекса нет."
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
