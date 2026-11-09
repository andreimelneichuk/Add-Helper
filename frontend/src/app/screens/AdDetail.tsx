import { useEffect, useState } from "react";
import type { ReworkVariant } from "../../api";
import type { Ad } from "../data";
import { adName, adShown, adStat, draftAd, inTest, useStoreState } from "../store";
import type { ReworkApi, State, Store } from "../store";
import { isWizardAdId } from "../live";
import { usePollingTask } from "../polling";
import { useObjectUrl } from "../useObjectUrl";
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
    <section className="anno-preview" aria-label={label ?? "Как увидит клиент"}>
      {label && <div className="ah-label anno-preview-label">{label}</div>}
      {ad.photo && <AdArt ad={ad} hook={hook} />}
      <div className="anno-preview-body">
        <div className="ann-title">{title}</div>
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
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const photo = useObjectUrl(photoFile);
  const [photoName, setPhotoName] = useState("");
  const [consent, setConsent] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setPhotoFile(file ?? null);
    setPhotoName(file?.name ?? "");
    setConsent(false);
  };

  const make = (request: string, tryNo = 0) => { setAsk(request); setAttempt(tryNo); setMade(draftAd(s, base, request, tryNo)); };
  const draft = made && "ad" in made ? made.ad : null;
  // Фото подходит и к прежнему тексту, поэтому своё фото само по себе уже вариант.
  const result = draft ?? (photo && base ? base : null);
  const final = result && photo ? { ...result, photo, facts: `${result.facts}, фото ваше` } : result;
  const blocked = !final || (!!photo && !consent);

  return (
    <section className="ah-panel anno-compose" aria-label={base ? "Переделать объявление" : "Новое объявление"}>
      <div role="group" aria-label={base ? "Что поменять" : "О чём объявление"}>
        <div className="ah-label">{base ? "Что поменять" : "О чём объявление"}</div>
        <div className="ah-chips">
          {(base ? STYLES : TOPICS).map((label) => (
            <button key={label} type="button" className="ah-chip" aria-pressed={ask === label} onClick={() => make(label)}>{label}</button>
          ))}
        </div>
      </div>
      <form className="anno-compose-ask" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) make(typed.trim()); }}>
        <label className="anno-compose-field">
          <span className="ah-label">Или напишите своими словами</span>
          <input className="ah-input" autoComplete="off" placeholder="Сделайте упор на запись сегодня" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </label>
        <button type="submit" className="ah-btn" disabled={!typed.trim()}>Сделать</button>
      </form>
      <div>
        <input id="anno-photo" type="file" accept="image/*" className="anno-compose-file" onChange={handleFileChange} />
        <label className="ah-btn" htmlFor="anno-photo">Своё фото</label>
        {photoName && <span className="ah-muted anno-compose-name">{photoName}</span>}
        {photo && (
          <label className="anno-compose-consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>На фото нет людей или у них есть согласие</span>
          </label>
        )}
      </div>
      {made && "refusal" in made && <p role="alert" className="ah-error">{made.refusal}</p>}
      {final && (
        <div className="anno-compare">
          {base && <Preview ad={base} hook={adShown(s, base).hook} title={adShown(s, base).title} label={current ?? "Сейчас в показах"} />}
          <Preview ad={final} hook={final.hook} title={final.title} label="Новый вариант" />
        </div>
      )}
      {final && <p className="ah-muted">Факты: {final.facts.toLowerCase()}. В прототипе вариант подставной, настоящий напишет ИИ.</p>}
      <div className="anno-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={blocked} onClick={() => final && onUse(final)}>{useLabel}</button>
        {draft && ask !== "Короче" && <button type="button" className="ah-btn" onClick={() => make(ask, attempt + 1)}>Другой вариант</button>}
        <button type="button" className="ah-btn" onClick={onCancel}>Отмена</button>
      </div>
    </section>
  );
}

const say = (e: unknown) => (e instanceof Error ? e.message : "Сервис не ответил. Попробуйте позже.");

type LiveReworkProps = { api: ReworkApi; ad: Ad; hook: string; title: string; waitMs: number; onDone: (text: string) => void; onCancel: () => void };

/** Переделка на сервисе: просьба уходит писателю, варианты проверены кодом, в показы — после выбора. */
export function LiveRework({ api, ad, hook, title, waitMs, onDone, onCancel }: LiveReworkProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [variants, setVariants] = useState<ReworkVariant[] | null>(null);
  const [setId, setSetId] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [account, adId] = ad.id.split("/") as [string, string];
  const poller = usePollingTask();

  const ask = async (wish: string) => {
    if (busy) return;
    setBusy(true);
    setProblem("");
    setVariants(null);
    setPicked(null);
    try {
      const { id } = await api.rework(account, adId, wish);
      setSetId(id);
      // Закрыли экран: запрос и таймер гасятся, ответ не придёт в размонтированный компонент.
      const polled = await poller.run({
        task: (signal) => api.reworkSet(id, { signal }),
        isDone: (view) => view.status === "failed" || view.status === "ready",
        intervalMs: waitMs,
        timeoutMs: Infinity,
      });
      if (polled.status !== "done") return;
      const view = polled.value;
      if (view.status === "failed") return setProblem(view.problem ?? "Вариантов нет. Старое объявление работает.");
      setVariants(view.variants);
      setPicked(view.variants[0]?.id ?? null);
    } catch (e) {
      setProblem(say(e));
    } finally {
      setBusy(false);
    }
  };
  const choose = async () => {
    if (setId === null || picked === null || busy) return;
    setBusy(true);
    try {
      const view = await api.chooseRework(setId, picked);
      onDone(view.text ?? "Новый вариант поставлен в очередь.");
    } catch (e) {
      setProblem(say(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ah-panel anno-compose" aria-label="Переделать объявление">
      <div role="group" aria-label="Что поменять">
        <div className="ah-label">Что поменять</div>
        <div className="ah-chips">
          {["Короче", "Упор на цену", "Упор на адрес"].map((label) => (
            <button key={label} type="button" className="ah-chip" disabled={busy} onClick={() => void ask(label)}>{label}</button>
          ))}
        </div>
      </div>
      <form className="anno-compose-ask" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) void ask(typed.trim()); }}>
        <label className="anno-compose-field">
          <span className="ah-label">Или напишите своими словами</span>
          <input className="ah-input" autoComplete="off" maxLength={200} placeholder="Сделайте упор на запись сегодня" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </label>
        <button type="submit" className="ah-btn" disabled={!typed.trim() || busy}>Сделать</button>
      </form>
      {busy && !variants && <p className="ah-muted" role="status">Пишу варианты и проверяю каждый: цены и обещания только из профиля. Обычно это меньше минуты.</p>}
      {problem && <p role="alert" className="ah-error">{problem}</p>}
      {variants && (
        <div className="anno-compare">
          <Preview ad={ad} hook={hook} title={title} label="Сейчас в показах" />
          <div role="radiogroup" aria-label="Новые варианты">
            {variants.map((v, i) => (
              <label key={v.id} className="anno-variant">
                <input type="radio" name="variant" checked={picked === v.id} onChange={() => setPicked(v.id)} />
                <Preview ad={{ ...ad, title: v.headline, text: v.text, photo: undefined }} hook={v.headline} title={v.headline} label={`Вариант ${i + 1}`} />
                {v.sources.length > 0 && <span className="ah-muted">Опирается на: {v.sources.map((f) => f.label.toLowerCase()).join(", ")}</span>}
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="anno-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={!variants || picked === null || busy} onClick={() => void choose()}>Поставить в показы</button>
        <button type="button" className="ah-btn" onClick={onCancel}>Отмена</button>
      </div>
    </section>
  );
}

export function AdDetail({ store, id }: { store: Store; id: string }) {
  const s = useStoreState(store);
  const ad = s.ads.find((a) => a.id === id);
  if (!ad) {
    return (
      <div className="anno-screen">
        <button type="button" className="ah-btn anno-back" onClick={() => store.set({ openAd: null })}>Все объявления</button>
        <p className="ah-muted">Объявление не найдено.</p>
      </div>
    );
  }
  const name = adName(s, id);
  const isArchived = ad.tag === "в архиве" || ad.tag === "заменено";
  const off = !isArchived && (!!s.adsOff[id] || ad.tag === "отключено");
  const working = s.working;
  const isWorkingOnThis = !!working && ("value" in working && working.value === id || working.action === "adSave" && working.value.id === id);
  const actionProgress = isWorkingOnThis && working
    ? working.action === "adPublish" ? "Публикуем объявление…"
      : working.action === "adArchive" ? "Архивируем объявление…"
        : working.action === "adRestore" ? "Восстанавливаем объявление…"
          : working.action === "adSave" ? "Сохраняем объявление…"
            : working.action === "ad" ? (off ? "Включаем объявление…" : "Отключаем объявление…") : null
    : null;
  const { hook, title } = adShown(s, ad);
  const [composing, setComposing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [publishOptions, setPublishOptions] = useState(false);
  const [editTitle, setEditTitle] = useState(ad.title);
  const [editText, setEditText] = useState(ad.text);

  // Подтверждённый вариант уже стал объявлением: форма больше не нужна.
  useEffect(() => {
    setComposing(false);
    setEditing(false);
    setEditTitle(ad.title);
    setEditText(ad.text);
  }, [ad.title, ad.text, ad.hook]);

  const events = s.hist.filter((h) => h.about === id || h.title.includes(`«${name}»`));
  const action = off ? "Включить" : "Отключить";
  const status = isArchived ? ad.tag : off ? "Отключено" : ad.tag;

  return (
    <div className="anno-screen">
      <button type="button" className="ah-btn anno-back" onClick={() => store.set({ openAd: null })}>Все объявления</button>
      <div className="anno-head">
        <h2 className="anno-name">{name}</h2>
        <span className={`ah-tag ah-tag-${off ? "neutral" : ad.tagTone}`}>{status}</span>
      </div>
      {editing ? (
        <form
          className="ah-panel anno-edit-form"
          style={{ maxWidth: 640, margin: "16px 0" }}
          onSubmit={(e) => {
            e.preventDefault();
            if (editTitle.trim() && editText.trim()) {
              store.ask({
                action: "adSave",
                value: { ...ad, title: editTitle.trim(), hook: editTitle.trim(), name: editTitle.trim(), text: editText.trim() },
              });
              setEditing(false);
            }
          }}
        >
          <div className="ah-panel-title">Редактировать объявление</div>
          <label className="anno-compose-field" style={{ display: "block", marginTop: 8 }}>
            <span>Заголовок ({editTitle.length}/56)</span>
            <input
              type="text"
              className="ah-input"
              maxLength={56}
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              required
            />
          </label>
          <label className="anno-compose-field" style={{ display: "block", marginTop: 12 }}>
            <span>Текст объявления ({editText.length}/81)</span>
            <textarea
              className="ah-input"
              rows={3}
              maxLength={81}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              required
            />
          </label>
          <div className="anno-actions" style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button type="submit" className="ah-btn ah-btn-primary" disabled={!editTitle.trim() || !editText.trim()}>Сохранить</button>
            <button type="button" className="ah-btn" onClick={() => setEditing(false)}>Отмена</button>
          </div>
        </form>
      ) : composing && s.live && store.rework ? (
        <LiveRework
          api={store.rework}
          ad={ad}
          hook={hook}
          title={title}
          waitMs={store.waitMs}
          onCancel={() => setComposing(false)}
          onDone={async (text) => {
            setComposing(false);
            store.set({ notice: text });
            if ("refresh" in store && typeof (store as any).refresh === "function") {
              await (store as any).refresh();
            }
          }}
        />
      ) : composing ? (
        <Compose s={s} base={ad} useLabel="Поставить в показы" onCancel={() => setComposing(false)} onUse={(value) => store.ask({ action: "adSave", value })} />
      ) : (
        <div className="anno-grid">
          <div className="ah-panel anno-show">
            <div className="ah-label">Так его видит клиент</div>
            <Preview ad={ad} hook={hook} title={title} />
          </div>
          <div className="anno-side">
            <section className="ah-panel anno-facts">
              <div className="anno-performance">
                <span className="ah-label">Результат объявления</span>
                <strong>{adStat(s, ad)}</strong>
              </div>
              {inTest(s, id) && <p className="ah-muted">Идёт проверка вариантов: решу на 14-й день.</p>}
              <div className="anno-fact-source">
                <div className="ah-label">На каких данных основано</div>
                <div>{ad.facts || "Проверяю"}</div>
              </div>
            </section>
            <section className="ah-panel anno-management">
              <h3 className="ah-panel-title">Действия с объявлением</h3>
              <div className="anno-actions" aria-busy={!!working}>
                {actionProgress && <p className="ah-muted" role="status">{actionProgress} Кнопки действий временно заблокированы.</p>}
                {isArchived ? (
                  <button type="button" className="ah-btn ah-btn-primary" disabled={!!working} onClick={() => store.ask({ action: "adRestore", value: id })}>Вернуть из архива</button>
                ) : (
                  <>
                    {ad.tag === "готово к запуску" && s.live?.hasCampaigns && (
                      <div className="anno-publish">
                        {!publishOptions ? (
                          <button type="button" className="ah-btn ah-btn-primary" disabled={!!working} onClick={() => setPublishOptions(true)}>Опубликовать</button>
                        ) : (
                          <div className="anno-publish-picker" role="group" aria-label="Как сгруппировать объявление">
                            <div>
                              <strong>Как добавить в кампанию?</strong>
                              <p className="ah-muted">Выберите, по каким группам услуг показывать это объявление.</p>
                            </div>
                            <button type="button" className="ah-btn ah-btn-primary" disabled={!!working} onClick={() => store.ask({ action: "adPublish", value: id, grouping: "services" })}>Разбить по услугам</button>
                            <button type="button" className="ah-btn" disabled={!!working} onClick={() => store.ask({ action: "adPublish", value: id, grouping: "general" })}>Одна общая группа</button>
                            <button type="button" className="ah-btn anno-publish-cancel" disabled={!!working} onClick={() => setPublishOptions(false)}>Назад</button>
                          </div>
                        )}
                      </div>
                    )}
                    {(!s.live?.hasCampaigns || isWizardAdId(id)) && (
                      <button type="button" className="ah-btn" disabled={!!working} onClick={() => { setEditTitle(ad.title); setEditText(ad.text); setEditing(true); }}>Редактировать</button>
                    )}
                    {(!s.live || store.rework) && <button type="button" className="ah-btn" disabled={!!working} onClick={() => setComposing(true)}>Переделать</button>}
                    <button type="button" className="ah-btn" disabled={!!working} onClick={() => (off ? store.run({ action: "ad", value: id }) : store.ask({ action: "ad", value: id }))}>{action}</button>
                    <button type="button" className="ah-btn" disabled={!!working} onClick={() => store.ask({ action: "adArchive", value: id })}>В архив</button>
                  </>
                )}
              </div>
            </section>
            <section className="ah-panel anno-events">
              <h3 className="ah-panel-title">Что я с ним делал</h3>
              {events.length === 0 && <p className="ah-muted">Пока ничего не менял.</p>}
              <ul>
                {events.map((h) => (
                  <li key={h.id}>
                    <div className="anno-events-when">{h.when}</div>
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
    <div className="anno-screen">
      <button type="button" className="ah-btn anno-back" onClick={() => store.set({ openAd: null })}>Все объявления</button>
      <h2 className="anno-name">Новое объявление</h2>
      <p className="ah-muted">Напишу из фактов вашего профиля или укажите пожелание. Общий бюджет не вырастет.</p>
      <Compose s={s} base={null} useLabel={s.live ? "Сохранить черновик" : "Поставить в показы"} onCancel={() => store.set({ openAd: null })} onUse={(value) => store.ask({ action: "adSave", value })} />
    </div>
  );
}
