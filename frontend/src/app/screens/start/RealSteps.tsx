// Шаги 5–8 мастера на настоящих данных: кабинет, маркировка, код статистики, сводка.
// Всё, что владелец вводит, сразу уходит в сервис; сводку готовности проверяет сервис.
import { useEffect, useState } from "react";
import type { AdAccount, AdvertiserKind, LaunchCheck, MarkingView, TagCheckView } from "../../../api";
import type { State, Store } from "../../store";

type Props = { store: Store; s: State };

const say = (e: unknown) => (e instanceof Error ? e.message : "Сервис не ответил. Попробуйте позже.");
const KIND_LABEL: Record<AdvertiserKind, string> = { legal: "Организация", ip: "ИП", person: "Физическое лицо" };
const KIND_NAME: Record<AdvertiserKind, string> = { legal: "Наименование, как в ЕГРЮЛ", ip: "ФИО предпринимателя", person: "ФИО" };

export function RealYandex({ store, s }: Props) {
  const api = store.wizard;
  const [accounts, setAccounts] = useState<AdAccount[] | null>(null);
  const [status, setStatus] = useState("");
  useEffect(() => {
    if (!api) return;
    api.accounts().then((found) => {
      setAccounts(found);
      store.set({ yandex: found.some((a) => a.state === "connected") });
    }, (e: unknown) => setStatus(say(e)));
  }, [api, store]);
  const connected = accounts?.find((a) => a.state === "connected");
  const connect = () => {
    if (!api) return;
    setStatus("");
    api.connect("start").then((r) => store.navigate(r.authorize_url), (e: unknown) => setStatus(say(e)));
  };
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">{connected ? `Кабинет подключён: ${connected.login}` : "Кабинет Директа"}</div>
        <div className="ah-muted">
          {s.account?.email ? `Вы вошли: ${s.account.email}. ` : ""}
          Профиль и бюджет сохранены в сервисе.{" "}
          {connected
            ? "Кампании запуск создаст в этом кабинете."
            : "Подключите кабинет, где будет реклама. Можно и позже, в настройках: без кабинета запуск не начнётся."}
        </div>
        {status && <p className="st-letter" role="status">{status}</p>}
      </div>
      {api && !connected && <button type="button" className="ah-btn ah-btn-primary" onClick={connect}>Подключить кабинет</button>}
    </div>
  );
}

export function RealMarking({ store, s }: Props) {
  const api = store.wizard;
  const [saved, setSaved] = useState<MarkingView | null>(null);
  useEffect(() => {
    if (!api) return;
    api.marking().then((r) => {
      setSaved(r.saved);
      if (r.saved) store.set({ marked: true });
    }, () => undefined);
  }, [api, store]);
  const a = s.advertiser;
  const edit = (patch: Partial<State["advertiser"]>) => store.set({ advertiser: { ...a, ...patch }, block: "" });
  if (s.marked && saved) {
    return (
      <div className="st-link">
        <div className="st-link-text">
          <div className="st-link-title">Сохранено: {KIND_LABEL[saved.kind].toLowerCase()}, ИНН {saved.inn}</div>
          <div className="ah-muted">{saved.name}. Передам в Яндекс при запуске, храню зашифрованным.</div>
        </div>
        <button type="button" className="ah-btn" onClick={() => store.set({ marked: false, advertiser: { ...a, kind: saved.kind, name: saved.name, okved: saved.okved ?? "" } })}>Изменить</button>
      </div>
    );
  }
  return (
    <div className="st-marking">
      <fieldset className="st-kind">
        <legend className="ah-label">Кто рекламодатель</legend>
        {(Object.keys(KIND_LABEL) as AdvertiserKind[]).map((k) => (
          <label key={k} className="st-kind-option">
            <input type="radio" name="advertiser-kind" checked={a.kind === k} onChange={() => edit({ kind: k })} /> {KIND_LABEL[k]}
          </label>
        ))}
      </fieldset>
      <label className="st-field st-inn">
        <span className="ah-label">ИНН для маркировки</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" maxLength={12} value={s.inn} placeholder={a.kind === "legal" ? "10 цифр" : "12 цифр"} onChange={(e) => store.set({ inn: e.target.value.replace(/\D/g, ""), block: "" })} />
      </label>
      <label className="st-field">
        <span className="ah-label">{KIND_NAME[a.kind]}</span>
        <input className="ah-input" autoComplete="off" maxLength={300} value={a.name} onChange={(e) => edit({ name: e.target.value })} />
      </label>
      {a.kind !== "person" && (
        <label className="st-field">
          <span className="ah-label">ОКВЭД основного вида деятельности</span>
          <input className="ah-input" inputMode="decimal" autoComplete="off" maxLength={8} value={a.okved} placeholder="например, 96.02" onChange={(e) => edit({ okved: e.target.value.replace(/[^\d.]/g, "") })} />
        </label>
      )}
      {a.kind === "person" && (
        <label className="st-field">
          <span className="ah-label">Телефон</span>
          <input className="ah-input" type="tel" autoComplete="off" maxLength={20} value={a.phone} placeholder="+7 900 000-00-00" onChange={(e) => edit({ phone: e.target.value })} />
        </label>
      )}
    </div>
  );
}

const TAG_TEXT = {
  found: (n: number[]) => `Код найден на сайте, счётчик ${n.join(", ")}. Что визиты доходят, проверю, когда подключите кабинет.`,
  missing: "На главной странице кода Метрики нет. Если код стоит через Google Tag Manager, я его не вижу: проверю по визитам после подключения кабинета.",
  failed: "Сайт не открылся при проверке. Попробуйте ещё раз чуть позже.",
  waiting: "Проверяю сайт, обычно до минуты.",
};

export function RealCode({ store, s }: Props) {
  const api = store.wizard;
  const [view, setView] = useState<TagCheckView | null>(null);
  const [status, setStatus] = useState("");
  const check = view?.check && view.check.url === view.site ? view.check : null;
  const waiting = check?.status === "queued" || check?.status === "running";
  useEffect(() => {
    if (!api) return;
    api.tagCheck().then(setView, (e: unknown) => setStatus(say(e)));
  }, [api]);
  // Пока проверка в очереди, спрашиваем сервис снова: воркер берёт её раз в минуту.
  useEffect(() => {
    if (!api || !waiting) return;
    const timer = window.setTimeout(() => { api.tagCheck().then(setView, (e: unknown) => setStatus(say(e))); }, store.waitMs);
    return () => window.clearTimeout(timer);
  }, [api, waiting, view, store.waitMs]);
  useEffect(() => { store.set({ metrica: !!view && (!view.site || check?.status === "found") }); }, [view, check, store]);
  if (!view) return <p className="ah-muted">{status || "Загружаю…"}</p>;
  if (!view.site) {
    return <p className="ah-muted">В профиле нет сайта. Обращения будут видны по звонкам и заявкам из Яндекса. Сайт можно добавить в настройках, тогда проверю код.</p>;
  }
  const letter = `Здравствуйте! Поставьте, пожалуйста, на сайт ${view.site} код счётчика Яндекс Метрики на все страницы. Без него реклама не увидит заявки и звонки.`;
  const run = () => {
    setStatus("");
    api?.requestTagCheck().then(setView, (e: unknown) => setStatus(say(e)));
  };
  const copy = () => { void navigator.clipboard?.writeText(letter).catch(() => undefined); store.set({ codeCopied: true }); };
  const found = check?.status === "found";
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Код статистики на {view.site}</div>
        <div className="ah-muted" role="status">
          {check ? (waiting ? TAG_TEXT.waiting : check.status === "found" ? TAG_TEXT.found(check.counters) : TAG_TEXT[check.status as "missing" | "failed"]) : "Поставьте код сами или перешлите письмо разработчику, а потом проверьте установку."}
        </div>
        {status && <p className="st-letter">{status}</p>}
        {s.codeCopied && !found && <p className="st-letter">{letter}</p>}
      </div>
      <div className="st-link-actions">
        <button type="button" className={found ? "ah-btn st-link-done" : "ah-btn ah-btn-primary"} disabled={waiting} onClick={run}>{found ? "Проверить снова" : "Проверить установку"}</button>
        {!found && <button type="button" className="ah-btn" onClick={copy}>{s.codeCopied ? "Письмо скопировано" : "Скопировать письмо разработчику"}</button>}
      </div>
    </div>
  );
}

export function RealLaunch({ store }: Props) {
  const api = store.wizard;
  const [check, setCheck] = useState<LaunchCheck | null>(null);
  const [status, setStatus] = useState("");
  useEffect(() => {
    if (!api) return;
    api.launchCheck().then(setCheck, (e: unknown) => setStatus(say(e)));
  }, [api]);
  if (!check) return <p className="ah-muted">{status || "Проверяю, всё ли готово…"}</p>;
  return (
    <>
      <ul className="st-ready" aria-label="Готовность к запуску">
        {check.items.map((item) => (
          <li key={item.key} className={item.ok ? "is-ok" : "is-missing"}>
            <span role="img" aria-label={item.ok ? "готово" : "не готово"}>{item.ok ? "✓" : "•"}</span> {item.text}
          </li>
        ))}
      </ul>
      <p className="st-letter" role="note">{check.ready ? "Всё нужное для запуска есть. " : "Чего не хватает, можно дополнить в настройках кабинета. "}{check.launch_text}</p>
    </>
  );
}
