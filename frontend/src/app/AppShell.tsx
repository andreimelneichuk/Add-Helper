import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import "@fontsource-variable/onest";
import "./tokens.css";
import type { Situation, View } from "./data";
import { BrandLogo, Icon } from "./icons";
import { LiveStore } from "./live";
import { Ads } from "./screens/Ads";
import { Chat } from "./screens/Chat";
import { History } from "./screens/History";
import { Overview } from "./screens/Overview";
import { Settings } from "./screens/Settings";
import { Start } from "./screens/Start";
import { Stats } from "./screens/Stats";
import { confirmText, initialState, statusText, stopped, Store, useStoreState, warned } from "./store";
import type { State } from "./store";

const VIEWS: [View, string][] = [["overview", "Обзор"], ["stats", "Статистика"], ["ads", "Реклама"], ["history", "История"]];
const SITUATIONS: [Situation, string][] = [["running", "всё хорошо"], ["first", "первые дни"], ["problem", "нет денег"], ["verdict", "вывод проверки"], ["budget", "бюджет на исходе"], ["rejected", "отказ Яндекса"], ["access", "нет доступа"]];
const SCREENS = { overview: Overview, stats: Stats, ads: Ads, history: History, settings: Settings };

function BusinessSelector({ store, s }: { store: Store; s: State }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    if (open) document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [open]);

  const liveStore = store instanceof LiveStore ? store : null;
  const currentName = s.live?.name ?? "Салон «Пример»";
  const businesses = s.businesses && s.businesses.length > 0 ? s.businesses : (s.live ? [{ id: s.activeBusinessId || "cur", name: currentName }] : []);

  return (
    <div ref={ref} className="ah-brand-selector">
      <button
        type="button"
        className="ah-brand-btn"
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={`Выбрать сайт (текущий: ${currentName})`}
        onClick={() => setOpen(!open)}
      >
        <span>{currentName}</span>
        <span className="ah-brand-arrow" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="ah-brand-menu" role="menu">
          <div className="ah-brand-menu-header">Ваши сайты</div>
          {businesses.map((b) => {
            const active = b.id === s.activeBusinessId || b.name === currentName;
            return (
              <button
                key={b.id}
                type="button"
                className={`ah-brand-menu-item ${active ? "is-active" : ""}`}
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  if (liveStore && b.id !== s.activeBusinessId) liveStore.switchBusiness(b.id);
                }}
              >
                <span className="ah-brand-check" aria-hidden="true">{active ? "✓ " : "  "}</span>
                <span>{b.name}</span>
              </button>
            );
          })}
          <div className="ah-brand-menu-divider" />
          <button
            type="button"
            className="ah-brand-menu-action"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              if (liveStore) liveStore.addNewSite();
              else window.location.assign("/?start=new");
            }}
          >
            <span>+ Добавить сайт</span>
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell({ store: given }: { store?: Store }) {
  const store = useMemo(() => {
    if (given) return given;
    const start = new URLSearchParams(window.location.search).has("start");
    return new Store(initialState(start ? "start" : "work"));
  }, [given]);
  const s = useStoreState(store);
  const starting = s.stage === "start";
  const chat = s.mode === "chat";
  const bad = (stopped(s) || (s.situation === "access" && !s.topped)) || s.paused;
  const tone = bad ? { background: "var(--bad-soft)", color: "var(--bad)" } : warned(s) ? { background: "var(--warn-soft)", color: "var(--warn)" } : { background: "var(--good-soft)", color: "var(--good)" };
  const tabs: [View, string][] = starting ? [["overview", "Настройка"]] : VIEWS;
  const Screen = SCREENS[s.view];
  const main = useRef<HTMLElement>(null);

  // Экран всегда открывается с начала: иначе тревога остаётся выше видимой части.
  useLayoutEffect(() => {
    if (main.current) main.current.scrollTop = 0;
  }, [s.view, s.mode, s.situation, s.step, s.stage, s.openAd]);

  return (
    <div className="ah">
      <header className={starting ? "ah-bar is-start" : "ah-bar"}>
        <strong className="ah-brand">
          {starting ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <BrandLogo size={22} />
              <span>Ad Helper</span>
            </span>
          ) : (
            <BusinessSelector store={store} s={s} />
          )}
        </strong>
        {starting && !s.account && (
          <button type="button" className="ah-btn" style={{ marginLeft: "auto" }} onClick={store.loginDirect}>
            Войти
          </button>
        )}
        {starting && s.account && (
          <span className="ah-muted" style={{ marginLeft: "auto", fontSize: "13px" }}>
            {s.account.email}
          </span>
        )}
        {!starting && <span className="ah-status" style={tone}><i aria-hidden="true" />{statusText(s)}</span>}
        {!starting && (s.live ? s.live.hasCampaigns : true) && <button type="button" className="ah-btn ah-bar-pause" onClick={() => store.run({ action: "pause" })}>{s.paused ? "Включить" : "Пауза"}</button>}
        {!starting && <button type="button" className="ah-btn ah-btn-icon" aria-label="Настройки" aria-current={!chat && s.view === "settings" ? "page" : undefined} onClick={() => store.goView("settings")}><Icon name="settings" /></button>}
      </header>
      {!starting && !s.live && (
        <div className="ah-proto">
          <span>ситуация:</span>
          {SITUATIONS.map(([id, label]) => (
            <button key={id} type="button" className="ah-chip" aria-pressed={s.situation === id} onClick={() => store.pickSituation(id)}>{label}</button>
          ))}
        </div>
      )}
      <div className="ah-body">
        <nav className="ah-rail" aria-label="Разделы">
          {tabs.map(([id, label]) => (
            <button key={id} type="button" aria-current={!chat && (starting || s.view === id) ? "page" : undefined} onClick={() => store.goView(id)}><Icon name={starting ? "settings" : id} />{label}</button>
          ))}
          <button type="button" aria-current={chat ? "page" : undefined} onClick={() => store.openChat()}><Icon name="chat" />Чат с ИИ</button>
        </nav>
        {chat ? <Chat store={store} /> : (
            <main ref={main} className="ah-main">
              {s.notice && !starting && (
                <div role="status" className="ah-notice"><span>{s.notice}</span><button type="button" className="ah-btn" onClick={() => store.set({ notice: "" })}>Понятно</button></div>
              )}
              {starting ? <Start store={store} /> : <Screen store={store} />}
              {s.pending && !starting && (
                <div role="group" aria-label="Подтверждение" className="ah-confirm">
                  <p>{confirmText(s, s.pending)}</p>
                  <div className="ah-confirm-actions">
                    <button type="button" className="ah-btn ah-btn-primary" onClick={store.confirm}>Применить</button>
                    <button type="button" className="ah-btn" autoFocus onClick={store.decline}>Не надо</button>
                  </div>
                </div>
              )}
            </main>
          )}
      </div>
      <nav className="ah-nav" aria-label="Разделы на телефоне">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" aria-current={!chat && (starting || s.view === id) ? "page" : undefined} onClick={() => store.goView(id)}><Icon name={starting ? "settings" : id} />{label}</button>
        ))}
        <button type="button" aria-current={chat ? "page" : undefined} onClick={() => store.openChat()}><Icon name="chat" />Чат</button>
      </nav>
    </div>
  );
}
