import { useEffect, useState } from "react";
import type { Ad } from "../data";
import { adName, adShown, adStat, draftAd, inTest, useStoreState } from "../store";
import type { State, Store } from "../store";
import "./AdDetail.css";

const STYLES = ["Короче", "Другой акцент"];
const TOPICS = ["Маникюр", "Педикюр", "Акция"];

/** Изображение объявления: своё фото или фраза на цветном фоне. */
export function AdArt({ ad, hook }: { ad: Ad; hook: string }) {
  if (!ad.photo) return <div className="ah-art">{hook}</div>;
  return <div className="ah-art ah-art-photo" style={{ backgroundImage: `linear-gradient(transparent 30%, rgb(0 0 0 / 65%)), url(${ad.photo})` }}>{hook}</div>;
}

function Preview({ ad, hook, title, label }: { ad: Ad; hook: string; title: string; label?: string }) {
  return (
    <section className="ad-preview" aria-label={label ?? "Как увидит клиент"}>
      {label && <div className="ah-label ad-preview-label">{label}</div>}
      <AdArt ad={ad} hook={hook} />
      <div className="ad-preview-body">
        <div className="ads-title">{title}</div>
        <div>{ad.text}</div>
      </div>
    </section>
  );
}

type ComposeProps = { s: State; base: Ad | null; current?: string; useLabel: string; onUse: (ad: Ad) => void; onCancel: () => void };

/** Просьба и вариант рядом со старым. Применяет не сам: решение за onUse. */
export function Compose({ s, base, current, useLabel, onUse, onCancel }: ComposeProps) {
  const [ask, setAsk] = useState("");
  const [typed, setTyped] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [made, setMade] = useState<ReturnType<typeof draftAd> | null>(null);
  const [photo, setPhoto] = useState<string | null>(null);
  const [photoName, setPhotoName] = useState("");
  const [consent, setConsent] = useState(false);

  const make = (request: string, tryNo = 0) => { setAsk(request); setAttempt(tryNo); setMade(draftAd(s, base, request, tryNo)); };
  const draft = made && "ad" in made ? made.ad : null;
  // Фото подходит и к прежнему тексту, поэтому своё фото само по себе уже вариант.
  const result = draft ?? (photo && base ? base : null);
  const final = result && photo ? { ...result, photo, facts: `${result.facts}, фото ваше` } : result;
  const blocked = !final || (!!photo && !consent);

  return (
    <section className="ah-panel ad-compose" aria-label={base ? "Переделать объявление" : "Новое объявление"}>
      <div role="group" aria-label={base ? "Что поменять" : "О чём объявление"}>
        <div className="ah-label">{base ? "Что поменять" : "О чём объявление"}</div>
        <div className="ah-chips">
          {(base ? STYLES : TOPICS).map((label) => (
            <button key={label} type="button" className="ah-chip" aria-pressed={ask === label} onClick={() => make(label)}>{label}</button>
          ))}
        </div>
      </div>
      <form className="ad-compose-ask" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) make(typed.trim()); }}>
        <label className="ad-compose-field">
          <span className="ah-label">Или напишите своими словами</span>
          <input className="ah-input" autoComplete="off" placeholder="Сделайте упор на запись сегодня" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </label>
        <button type="submit" className="ah-btn" disabled={!typed.trim()}>Сделать</button>
      </form>
      <div>
        <input id="ad-photo" type="file" accept="image/*" className="ad-compose-file" onChange={(e) => { const file = e.target.files?.[0]; setPhoto(file ? URL.createObjectURL(file) : null); setPhotoName(file?.name ?? ""); setConsent(false); }} />
        <label className="ah-btn" htmlFor="ad-photo">Своё фото</label>
        {photoName && <span className="ah-muted ad-compose-name">{photoName}</span>}
        {photo && (
          <label className="ad-compose-consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>На фото нет людей или у них есть согласие</span>
          </label>
        )}
      </div>
      {made && "refusal" in made && <p role="alert" className="ah-error">{made.refusal}</p>}
      {final && (
        <div className="ad-compare">
          {base && <Preview ad={base} hook={adShown(s, base).hook} title={adShown(s, base).title} label={current ?? "Сейчас в показах"} />}
          <Preview ad={final} hook={final.hook} title={final.title} label="Новый вариант" />
        </div>
      )}
      {final && <p className="ah-muted">Факты: {final.facts.toLowerCase()}. В прототипе вариант подставной, настоящий напишет ИИ.</p>}
      <div className="ad-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={blocked} onClick={() => final && onUse(final)}>{useLabel}</button>
        {draft && ask !== "Короче" && <button type="button" className="ah-btn" onClick={() => make(ask, attempt + 1)}>Другой вариант</button>}
        <button type="button" className="ah-btn" onClick={onCancel}>Отмена</button>
      </div>
    </section>
  );
}

export function AdDetail({ store, id }: { store: Store; id: string }) {
  const s = useStoreState(store);
  const ad = s.ads.find((a) => a.id === id)!;
  const name = adName(s, id);
  const off = !!s.adsOff[id];
  const { hook, title } = adShown(s, ad);
  const [composing, setComposing] = useState(false);
  // Подтверждённый вариант уже стал объявлением: форма больше не нужна.
  useEffect(() => setComposing(false), [ad]);
  const events = s.hist.filter((h) => h.about === id || h.title.includes(`«${name}»`));
  const action = off ? "Включить" : "Отключить";

  return (
    <div className="ad-screen">
      <button type="button" className="ah-btn ad-back" onClick={() => store.set({ openAd: null })}>Все объявления</button>
      <div className="ad-head">
        <h2 className="ad-name">{name}</h2>
        <span className={`ah-tag ah-tag-${ad.tagTone}`}>{ad.tag}</span>
      </div>
      {composing ? (
        <Compose s={s} base={ad} useLabel="Поставить в показы" onCancel={() => setComposing(false)} onUse={(value) => store.ask({ action: "adSave", value })} />
      ) : (
        <div className="ad-grid">
          <div className="ah-panel ad-show">
            <div className="ah-label">Так его видит клиент</div>
            <Preview ad={ad} hook={hook} title={title} />
          </div>
          <div className="ad-side">
            <section className="ah-panel ad-facts">
              <div className="ah-panel-title">{adStat(s, ad)}</div>
              {inTest(s, id) && <p className="ah-muted">Идёт проверка вариантов: решу на 14-й день.</p>}
              <div>
                <div className="ah-label">Откуда факты</div>
                <div>{ad.facts || "Проверяю"}</div>
              </div>
              <div className="ad-actions">
                {!s.live && <button type="button" className="ah-btn ah-btn-primary" onClick={() => setComposing(true)}>Переделать</button>}
                <button type="button" className="ah-btn" onClick={() => (off ? store.run({ action: "ad", value: id }) : store.ask({ action: "ad", value: id }))}>{action}</button>
              </div>
            </section>
            <section className="ah-panel ad-events">
              <h3 className="ah-panel-title">Что я с ним делал</h3>
              {events.length === 0 && <p className="ah-muted">Пока ничего не менял.</p>}
              <ul>
                {events.map((h) => (
                  <li key={h.id}>
                    <div className="ad-events-when">{h.when}</div>
                    <div>{h.title}</div>
                    <div className="ah-muted">{h.why}</div>
                  </li>
                ))}
              </ul>
              {events.length > 0 && <button type="button" className="ah-btn" onClick={() => store.goView("history")}>Вся история и отмена</button>}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

export function AdMaker({ store }: { store: Store }) {
  const s = useStoreState(store);
  return (
    <div className="ad-screen">
      <button type="button" className="ah-btn ad-back" onClick={() => store.set({ openAd: null })}>Все объявления</button>
      <h2 className="ad-name">Новое объявление</h2>
      <p className="ah-muted">Напишу из фактов вашего профиля. Общий бюджет не вырастет.</p>
      <Compose s={s} base={null} useLabel="Поставить в показы" onCancel={() => store.set({ openAd: null })} onUse={(value) => store.ask({ action: "adSave", value })} />
    </div>
  );
}
