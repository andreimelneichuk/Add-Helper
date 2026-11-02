// Настройки на настоящих данных: профиль, уведомления, кабинет Яндекса и выход.
// Правка профиля не трогает площадку, поэтому идёт прямо в API, а не через журнал.
import { useCallback, useEffect, useState } from "react";
import type { AdAccount, Api, Channels as ChannelsData, Profile } from "../../api";
import { rub } from "../data";
import { liveApi } from "../live";
import type { LiveStore } from "../live";
import { useStoreState } from "../store";
import type { Store } from "../store";
import "./Settings.css";

// Пока владелец открывает бота, проверяем привязку сами: бот читает сообщения раз в минуту.
const WAIT_MS = 10_000;

const ACCOUNT_STATE: Record<string, string> = {
  connected: "Подключён",
  needs_reconnect: "Нет доступа, подключите заново",
  disconnected: "Отключён",
};

/** Итог подключения кабинета: Яндекс возвращает владельца на `/?connect=…`. */
export const CONNECT_RESULT: Record<string, string> = {
  ok: "Кабинет Яндекса подключён.",
  denied: "Вы отменили подключение в Яндексе. Ничего не изменилось.",
  taken: "Этот кабинет уже подключён к другому бизнесу. Ничего не изменилось.",
  failed: "Подключить не получилось: ссылка устарела или Яндекс отказал. Попробуйте ещё раз.",
  unavailable: "Подключение кабинета пока не настроено на сервере.",
};

const message = (e: unknown) => (e instanceof Error ? e.message : "Сервис не ответил. Попробуйте позже.");

function BudgetForm({ store }: { store: LiveStore }) {
  const s = useStoreState(store);
  const live = s.live!;
  const [draft, setDraft] = useState(live.budget > 0 ? String(live.budget) : "");
  const [asking, setAsking] = useState(false);
  const amount = Number(draft);
  const spent = live.spent ?? 0;
  const problem = !draft ? "Укажите сумму." : amount < spent ? `В этом месяце уже потрачено ${rub(spent)}, бюджет не может быть меньше.` : null;
  const lower = amount < live.budget;
  if (live.budget <= 0) return <><div><div className="ah-label">Бюджет в месяц</div><div>Ещё не задан</div></div><p className="set-hint">Бюджет задаётся при запуске рекламы.</p></>;
  return (
    <form className="set-form" aria-label="Бюджет месяца" onSubmit={(e) => { e.preventDefault(); if (!problem && amount !== live.budget) setAsking(true); }}>
      <label>
        <span className="ah-label">Бюджет в месяц, ₽</span>
        <input className="ah-input" inputMode="numeric" value={draft} onChange={(e) => { setAsking(false); setDraft(e.target.value.replace(/\D/g, "").slice(0, 9)); }} />
      </label>
      <p className="set-hint">Сейчас {rub(live.budget)}, потрачено {rub(spent)}. Яндекс не потратит больше: лимиты стоят в самом кабинете.</p>
      {draft && problem && <p className="ah-error" role="alert">{problem}</p>}
      {asking ? (
        <div className="set-confirm" role="group" aria-label="Подтверждение бюджета">
          <p>
            {`Поставить ${rub(amount)} в месяц вместо ${rub(live.budget)}? `}
            {lower ? "Сначала снижу лимиты в Яндексе, потом сменю бюджет: так расход не выйдет за новую сумму ни в какой момент." : "Лимиты в Яндексе не изменятся сразу: автопилот поднимает их по правилам, понемногу."}
          </p>
          <div className="set-actions">
            <button type="button" className="ah-btn ah-btn-primary" onClick={() => { setAsking(false); store.run({ action: "setting", value: ["budget", amount] }); }}>Применить</button>
            <button type="button" className="ah-btn" onClick={() => { setAsking(false); setDraft(String(live.budget)); }}>Не менять</button>
          </div>
        </div>
      ) : (
        <div className="set-actions"><button type="submit" className="ah-btn" disabled={!!problem || amount === live.budget}>Изменить бюджет</button></div>
      )}
    </form>
  );
}

function ProfileForm({ api, store }: { api: Api; store: LiveStore }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.profile().then(setProfile, (e: unknown) => setStatus(message(e)));
  }, [api]);
  if (!profile) return <p className="ah-muted">{status ?? "Загружаю профиль…"}</p>;

  const set = (patch: Partial<Profile>) => {
    setProfile({ ...profile, ...patch });
    setStatus(null);
  };
  const service = (i: number, patch: Partial<Profile["services"][number]>) =>
    set({ services: profile.services.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const save = async () => {
    setBusy(true);
    try {
      const services = profile.services.filter((x) => x.name.trim());
      setProfile(await api.saveProfile({ ...profile, services, features: profile.features.map((f) => f.trim()).filter(Boolean) }));
      await store.refresh();
      setStatus("Сохранил. Новые объявления будут писаться по этим фактам, уже идущие не меняются.");
    } catch (e) {
      setStatus(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="set-form">
      <label><span className="ah-label">Чем занимаетесь</span><input className="ah-input" value={profile.niche} placeholder="маникюр" onChange={(e) => set({ niche: e.target.value })} /></label>
      <label><span className="ah-label">Адрес</span><input className="ah-input" value={profile.address} onChange={(e) => set({ address: e.target.value })} /></label>
      <label><span className="ah-label">Сайт</span><input className="ah-input" value={profile.website} placeholder="https://" onChange={(e) => set({ website: e.target.value })} /></label>
      <fieldset className="set-services">
        <legend className="ah-label">Услуги и цены</legend>
        {profile.services.map((x, i) => (
          <div key={i} className="set-service">
            <input className="ah-input" aria-label={`Услуга ${i + 1}`} value={x.name} onChange={(e) => service(i, { name: e.target.value })} />
            <input className="ah-input set-price" aria-label={`Цена услуги ${i + 1}, ₽`} inputMode="numeric" placeholder="₽" value={x.price ?? ""} onChange={(e) => { const d = e.target.value.replace(/\D/g, "").slice(0, 7); service(i, { price: d ? Number(d) : null }); }} />
            <button type="button" className="ah-link" aria-label={`Убрать услугу ${i + 1}`} onClick={() => set({ services: profile.services.filter((_, j) => j !== i) })}>Убрать</button>
          </div>
        ))}
        <button type="button" className="ah-btn" onClick={() => set({ services: [...profile.services, { name: "", price: null }] })}>Добавить услугу</button>
      </fieldset>
      <label>
        <span className="ah-label">Чем вы отличаетесь, по одному факту в строке</span>
        <textarea className="ah-input set-textarea" rows={4} value={profile.features.join("\n")} onChange={(e) => set({ features: e.target.value.split("\n") })} />
      </label>
      <div className="set-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={save}>Сохранить</button>
        {status && <span role="status" className="set-hint">{status}</span>}
      </div>
    </div>
  );
}

function Channels({ api }: { api: Api }) {
  const [channels, setChannels] = useState<ChannelsData | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.channels().then(setChannels, (e: unknown) => setError(message(e))), [api]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!link || channels?.telegram.linked) return;
    const timer = window.setInterval(load, WAIT_MS);
    return () => window.clearInterval(timer);
  }, [link, channels, load]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  if (!channels) return <p className="ah-muted">{error ?? "Загружаю…"}</p>;
  const { telegram } = channels;
  const telegramText = !telegram.available
    ? "Бот пока не настроен на сервере."
    : telegram.linked
      ? "Тревоги приходят и в Telegram."
      : link
        ? "Откройте бота и нажмите «Запустить». Ссылка действует 15 минут, подключение я проверю сам."
        : "Тревога придёт сразу в телефон: письмо можно не заметить.";

  return (
    <div className="set-rows">
      <div className="set-row">
        <div><strong>Почта</strong><p className="set-hint">{channels.email ?? "В Яндекс ID нет почты, письма не придут. Добавьте почту в Яндекс ID и войдите заново."}</p></div>
        <span className={channels.email ? "ah-tag ah-tag-good" : "ah-tag ah-tag-bad"}>{channels.email ? "Включена" : "Нет адреса"}</span>
      </div>
      <div className="set-row">
        <div>
          <strong>Telegram</strong>
          <p className="set-hint">{telegramText}</p>
          {link && !telegram.linked && <a className="ah-link" href={link} target="_blank" rel="noopener noreferrer">Открыть бота ещё раз</a>}
        </div>
        {telegram.available && (telegram.linked
          ? <button type="button" className="ah-btn" disabled={busy} onClick={() => act(async () => { await api.unlinkTelegram(); setLink(null); await load(); })}>Отключить</button>
          : <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={() => act(async () => { const { url } = await api.linkTelegram(); setLink(url); window.open(url, "_blank", "noopener"); })}>Подключить</button>)}
      </div>
      {error && <p className="ah-error" role="alert">{error}</p>}
    </div>
  );
}

function Accounts({ api }: { api: Api }) {
  const [accounts, setAccounts] = useState<AdAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.accounts().then(setAccounts, (e: unknown) => setError(message(e)));
  }, [api]);
  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      const { authorize_url } = await api.connect();
      window.location.assign(authorize_url);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };
  const working = accounts?.some((a) => a.state === "connected");
  return (
    <div className="set-rows">
      {accounts === null && !error && <p className="ah-muted">Загружаю…</p>}
      {accounts?.length === 0 && <p className="set-hint">Кабинет ещё не подключён: без него реклама не запустится. Деньги списываются с вашего счёта в Яндексе, мы их не держим.</p>}
      {accounts?.map((a) => (
        <div key={a.id} className="set-row">
          <div><strong>{a.login}</strong><p className="set-hint">Яндекс Директ</p></div>
          <span className={a.state === "connected" ? "ah-tag ah-tag-good" : "ah-tag ah-tag-bad"}>{ACCOUNT_STATE[a.state] ?? a.state}</span>
        </div>
      ))}
      {accounts !== null && (
        <div><button type="button" className={working ? "ah-btn" : "ah-btn ah-btn-primary"} disabled={busy} onClick={connect}>{accounts.length ? "Подключить заново" : "Подключить кабинет"}</button></div>
      )}
      {error && <p className="ah-error" role="alert">{error}</p>}
    </div>
  );
}

export function LiveSettings({ store }: { store: Store }) {
  const api = liveApi(store)!;
  const [leaving, setLeaving] = useState<string | null>(null);
  const logout = async () => {
    setLeaving("Выхожу…");
    try {
      await api.logout();
      window.location.assign("/");
    } catch (e) {
      setLeaving(message(e));
    }
  };
  return (
    <div className="set">
      <div className="set-main">
        <section className="ah-panel set-section" aria-labelledby="set-title">
          <h2 id="set-title" className="ah-panel-title">Бюджет</h2>
          <BudgetForm store={store as LiveStore} />
          <p className="set-hint">Район и время показов отсюда пока не меняются: эти настройки ещё не подключены к сервису.</p>
        </section>
        <section className="ah-panel set-section" aria-labelledby="set-profile-title">
          <h2 id="set-profile-title" className="ah-panel-title">Профиль бизнеса</h2>
          <p className="set-hint">В рекламу попадают только эти факты. Чего здесь нет, того в объявлениях не будет.</p>
          <ProfileForm api={api} store={store as LiveStore} />
        </section>
      </div>
      <div className="set-main">
        <section className="ah-panel set-section" aria-labelledby="set-account-title">
          <h2 id="set-account-title" className="ah-panel-title">Кабинет Яндекса</h2>
          <Accounts api={api} />
        </section>
        <section className="ah-panel set-section" aria-labelledby="set-channels-title">
          <h2 id="set-channels-title" className="ah-panel-title">Уведомления</h2>
          <p className="set-hint">Напишу, если показы остановлены или с деньгами что-то не так.</p>
          <Channels api={api} />
        </section>
        <section className="ah-panel set-section" aria-labelledby="set-exit-title">
          <h2 id="set-exit-title" className="ah-panel-title">Вход</h2>
          <div><button type="button" className="ah-btn" disabled={leaving === "Выхожу…"} onClick={logout}>Выйти</button></div>
          {leaving && leaving !== "Выхожу…" && <p className="ah-error" role="alert">{leaving}</p>}
        </section>
      </div>
    </div>
  );
}
