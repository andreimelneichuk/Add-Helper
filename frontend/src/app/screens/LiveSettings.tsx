// Настройки на настоящих данных: профиль, уведомления, кабинет Яндекса и выход.
// Правка профиля не трогает площадку, поэтому идёт прямо в API, а не через журнал.
import { useCallback, useEffect, useState } from "react";
import type { AdAccount, Api, Channels as ChannelsData, Display, Profile, SiteFact, SiteRead } from "../../api";
import { rub } from "../data";
import { liveApi, savedText } from "../live";
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

/** До запуска периода нет: показываем план из мастера. Лимиты в Яндексе из него ставятся только при запуске. */
function PlanForm({ api }: { api: Api }) {
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.budgetPlan().then((p) => setDraft(p.rubles ? String(p.rubles) : ""), (e: unknown) => setStatus(message(e)));
  }, [api]);
  const amount = Number(draft);
  return (
    <form className="set-form" aria-label="План бюджета" onSubmit={(e) => {
      e.preventDefault();
      if (!draft || busy) return;
      setBusy(true);
      api.saveBudgetPlan(amount).then((p) => setStatus(`Сохранил план: ${rub(p.rubles ?? amount)} в месяц. Лимиты в Яндексе поставлю при запуске рекламы.`), (err: unknown) => setStatus(message(err))).finally(() => setBusy(false));
    }}>
      <label>
        <span className="ah-label">План на запуск, ₽ в месяц</span>
        <input className="ah-input" inputMode="numeric" value={draft} onChange={(e) => { setStatus(""); setDraft(e.target.value.replace(/\D/g, "").slice(0, 9)); }} />
      </label>
      <p className="set-hint">Реклама ещё не запущена, денег не тратим. Лимиты в Яндексе поставлю при запуске, на эту сумму.</p>
      {status && <p className="set-hint" role="status">{status}</p>}
      <div className="set-actions"><button type="submit" className="ah-btn" disabled={!draft || busy}>Сохранить план</button></div>
    </form>
  );
}

function BudgetForm({ store }: { store: LiveStore }) {
  const s = useStoreState(store);
  const live = s.live!;
  const [draft, setDraft] = useState(live.budget > 0 ? String(live.budget) : "");
  const [asking, setAsking] = useState(false);
  const amount = Number(draft);
  const spent = live.spent ?? 0;
  const problem = !draft ? "Укажите сумму." : amount < spent ? `В этом месяце уже потрачено ${rub(spent)}, бюджет не может быть меньше.` : null;
  const lower = amount < live.budget;
  if (live.budget <= 0) return <PlanForm api={store.api} />;
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

const DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const HOURS = Array.from({ length: 25 }, (_, h) => h);
/** Те же слова, что пишет сервис в историю: «пн–сб, 10:00–21:00». */
export function describeSchedule(days: number[], [start, end]: [number, number]): string {
  if (days.length === 7 && start === 0 && end === 24) return "круглосуточно";
  const first = days[0] ?? 0;
  const last = days[days.length - 1] ?? 0;
  const span = days.length > 2 && last - first + 1 === days.length;
  const when = days.length === 7 ? "каждый день" : span ? `${DAY_NAMES[first]}–${DAY_NAMES[last]}` : days.map((d) => DAY_NAMES[d]).join(", ");
  return `${when}, ${start}:00–${end}:00`;
}
type Ask = { text: string; apply: () => Promise<string> } | null;

/** Где и когда показывать. Меняется в Яндексе через журнал, поэтому с подтверждением и отменой в истории. */
function DisplayForm({ api, store }: { api: Api; store: LiveStore }) {
  const [display, setDisplay] = useState<Display | null>(null);
  const [regions, setRegions] = useState<number[]>([]);
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const [hours, setHours] = useState<[number, number]>([0, 24]);
  const [goal, setGoal] = useState("");
  const [ask, setAsk] = useState<Ask>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const d = await api.display();
    setDisplay(d);
    setRegions(d.regions ?? []);
    if (d.schedule) { setDays(d.schedule.days); setHours([d.schedule.start, d.schedule.end]); }
    setGoal(d.goal ? String(d.goal) : "");
  }, [api]);
  useEffect(() => { load().catch((e: unknown) => setStatus(message(e))); }, [load]);
  if (!display) return <p className="ah-muted">{status ?? "Загружаю…"}</p>;

  const names = (ids: number[]) => display.catalog.filter((r) => ids.includes(r.id)).map((r) => r.name).join(", ");
  const weeklyHours = days.length * Math.max(0, hours[1] - hours[0]);
  const scheduleProblem = !days.length ? "Выберите хотя бы один день." : hours[0] >= hours[1] ? "Начало должно быть раньше конца." : weeklyHours < display.min_weekly_hours ? `Яндекс не принимает меньше ${display.min_weekly_hours} часов показа в неделю, сейчас ${weeklyHours}.` : null;
  const sameSchedule = !!display.schedule && display.schedule.days.join() === days.join() && display.schedule.start === hours[0] && display.schedule.end === hours[1];
  const sameRegions = !!display.regions && display.regions.join() === [...regions].sort((a, b) => a - b).join();
  const scheduleText = describeSchedule(days, hours);
  const goalValue = Number(goal);

  const confirm = async () => {
    if (!ask) return;
    setBusy(true);
    try {
      const text = await ask.apply();
      await load();
      await store.refresh();
      setStatus(text);
    } catch (e) {
      setStatus(message(e));
    } finally {
      setAsk(null);
      setBusy(false);
    }
  };
  const toggle = <T,>(list: T[], value: T) => (list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  const noCampaigns = display.campaigns === 0;

  return (
    <div className="set-form">
      {noCampaigns && <p className="set-hint">Кампаний ещё нет: регион и часы зададутся при запуске.</p>}
      <div role="group" aria-label="Где показывать">
        <div className="ah-label">Где показывать</div>
        <p className="set-hint">Сейчас: {display.regions_text ?? "у направлений разный регион или Яндекс его ещё не передал"}.</p>
        <div className="set-options">
          {display.catalog.map((r) => (
            <button key={r.id} type="button" className="ah-chip" aria-pressed={regions.includes(r.id)} onClick={() => { setStatus(null); setRegions(toggle(regions, r.id)); }}>{r.name}</button>
          ))}
        </div>
        <div className="set-actions">
          <button type="button" className="ah-btn" disabled={noCampaigns || !regions.length || sameRegions || busy} onClick={() => setAsk({ text: `Показывать рекламу: ${names(regions)}${display.regions_text ? ` вместо «${display.regions_text}»` : ""}? Это изменится в Яндексе для всех направлений, отменить можно в истории.`, apply: async () => { await api.setRegions(regions, crypto.randomUUID()); return `Регион показов теперь: ${names(regions)}. Вы попросили. Отменить можно в истории.`; } })}>Изменить регион</button>
        </div>
      </div>
      <div role="group" aria-label="Когда показывать">
        <div className="ah-label">Когда показывать</div>
        <p className="set-hint">Сейчас: {display.schedule_text ?? "у направлений разное время или Яндекс его ещё не передал"}.</p>
        <div className="set-options">
          {DAY_NAMES.map((name, d) => (
            <button key={name} type="button" className="ah-chip" aria-pressed={days.includes(d)} onClick={() => { setStatus(null); setDays(toggle(days, d).sort((a, b) => a - b)); }}>{name}</button>
          ))}
        </div>
        <div className="set-options">
          <label><span className="ah-label">С</span> <select className="ah-input" aria-label="Показывать с" value={hours[0]} onChange={(e) => setHours([Number(e.target.value), hours[1]])}>{HOURS.slice(0, 24).map((h) => <option key={h} value={h}>{h}:00</option>)}</select></label>
          <label><span className="ah-label">До</span> <select className="ah-input" aria-label="Показывать до" value={hours[1]} onChange={(e) => setHours([hours[0], Number(e.target.value)])}>{HOURS.slice(1).map((h) => <option key={h} value={h}>{h}:00</option>)}</select></label>
        </div>
        {scheduleProblem && <p className="ah-error" role="alert">{scheduleProblem}</p>}
        <div className="set-actions">
          <button type="button" className="ah-btn" disabled={noCampaigns || !!scheduleProblem || sameSchedule || busy} onClick={() => setAsk({ text: `Показывать рекламу ${scheduleText}${display.schedule_text ? ` вместо «${display.schedule_text}»` : ""}? Это изменится в Яндексе для всех направлений, отменить можно в истории.`, apply: async () => { await api.setSchedule(days, hours[0], hours[1], crypto.randomUUID()); return `Показываю рекламу ${scheduleText}. Вы попросили. Отменить можно в истории.`; } })}>Изменить время</button>
        </div>
      </div>
      {ask && (
        <div className="set-confirm" role="group" aria-label="Подтверждение показа">
          <p>{ask.text}</p>
          <div className="set-actions">
            <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={confirm}>Применить</button>
            <button type="button" className="ah-btn" disabled={busy} onClick={() => setAsk(null)}>Не менять</button>
          </div>
        </div>
      )}
      <form className="set-form" aria-label="Желаемая цена обращения" onSubmit={(e) => {
        e.preventDefault();
        if (goalValue < 1 || goalValue === display.goal) return;
        setBusy(true);
        api.setGoal(goalValue).then(async () => { await load(); setStatus(`Желаемая цена обращения теперь ${rub(goalValue)}. Это мерка для автопилота: объявление, где обращение выходит заметно дороже, он отключает. Поменять можно здесь же.`); }, (err: unknown) => setStatus(message(err))).finally(() => setBusy(false));
      }}>
        <label>
          <span className="ah-label">Желаемая цена обращения, ₽</span>
          <input className="ah-input" inputMode="numeric" value={goal} placeholder={display.goal ? undefined : "Например, 400"} onChange={(e) => { setStatus(null); setGoal(e.target.value.replace(/\D/g, "").slice(0, 7)); }} />
        </label>
        <p className="set-hint">{display.goal ? `Сейчас ${rub(display.goal)}.` : "Пока не задана."} В Яндексе это ничего не меняет: это мерка для решений автопилота.</p>
        <div className="set-actions"><button type="submit" className="ah-btn" disabled={busy || goalValue < 1 || goalValue === display.goal}>Задать цену</button></div>
      </form>
      {status && <p role="status" className="set-hint">{status}</p>}
    </div>
  );
}

// Разбор идёт в воркере раз в минуту; чаще спрашивать незачем.
const SITE_WAIT_MS = 3_000;
const MAX_SERVICES = 30;
const MAX_FEATURES = 20;
const FACT_LABEL: Record<SiteFact["field"], string> = {
  niche: "Чем занимаетесь",
  address: "Адрес",
  service: "Услуга",
  feature: "Особенность",
};

/** Черновик с сайта в форме профиля. Сохраняет владелец сам: в рекламу идёт только сохранённое. */
export function mergeFacts(profile: Profile, facts: SiteFact[], url: string): Profile {
  const services = [...profile.services];
  const features = [...profile.features];
  let { niche, address } = profile;
  for (const fact of facts) {
    if (fact.field === "niche") niche = fact.value;
    else if (fact.field === "address") address = fact.value;
    else if (fact.field === "service") {
      const i = services.findIndex((x) => x.name.trim().toLowerCase() === fact.value.trim().toLowerCase());
      const same = services[i];
      // Отмеченная цена — выбор владельца: она заменяет прежнюю.
      if (same) services[i] = { ...same, price: fact.price ?? same.price };
      else if (services.length < MAX_SERVICES) services.push({ name: fact.value, price: fact.price });
    } else if (!features.some((f) => f.trim().toLowerCase() === fact.value.trim().toLowerCase()) && features.length < MAX_FEATURES) {
      features.push(fact.value);
    }
  }
  return { ...profile, services, features, niche, address, website: profile.website.trim() || url };
}

function SiteDraft({ read, onApply, onClose }: { read: SiteRead; onApply: (facts: SiteFact[]) => void; onClose: () => void }) {
  const [chosen, setChosen] = useState<boolean[]>(() => read.facts.map(() => true));
  if (read.status === "failed") return <p role="alert" className="set-hint">{read.problem_text ?? "Не получилось разобрать сайт. Заполните профиль вручную."}</p>;
  if (read.facts.length === 0) return <p role="alert" className="set-hint">На сайте не нашёл фактов, которые можно подтвердить цитатой. Заполните профиль вручную.</p>;
  return (
    <div className="set-confirm" role="group" aria-label="Нашёл на сайте">
      <p>Вот что я нашёл на сайте. Под каждым фактом цитата со страницы. Снимите отметку с того, что неверно или устарело.</p>
      <ul className="set-facts">
        {read.facts.map((fact, i) => (
          <li key={i}>
            <label>
              <input type="checkbox" checked={chosen[i]} onChange={() => setChosen(chosen.map((c, j) => (j === i ? !c : c)))} />{" "}
              {FACT_LABEL[fact.field]}: {fact.value}{fact.price !== null ? `, ${rub(fact.price)}` : ""}
            </label>
            <div className="ah-muted set-quote">«{fact.quote}»</div>
          </li>
        ))}
      </ul>
      <div className="set-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={!chosen.some(Boolean)} onClick={() => onApply(read.facts.filter((_, i) => chosen[i]))}>Перенести в профиль</button>
        <button type="button" className="ah-btn" onClick={onClose}>Не переносить</button>
      </div>
    </div>
  );
}

function ProfileForm({ api, store }: { api: Api; store: LiveStore }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Первый пришедший профиль и есть форма: поздний ответ не затирает то, что владелец уже ввёл.
  useEffect(() => {
    api.profile().then((loaded) => setProfile((current) => current ?? loaded), (e: unknown) => setStatus(message(e)));
  }, [api]);
  const [read, setRead] = useState<SiteRead | null>(null);
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const poll = useCallback(async () => {
    try {
      const latest = await api.siteRead();
      const waiting = latest !== null && (latest.status === "queued" || latest.status === "running");
      setReading(waiting);
      if (latest && !waiting) setRead(latest);
    } catch (e) {
      setReading(false);
      setReadError(message(e));
    }
  }, [api]);
  // Разбор, начатый раньше, досматриваем, а готовый старый черновик не навязываем.
  useEffect(() => {
    api.siteRead().then((latest) => setReading(latest !== null && (latest.status === "queued" || latest.status === "running")), () => undefined);
  }, [api]);
  useEffect(() => {
    if (!reading) return;
    const timer = window.setInterval(() => void poll(), SITE_WAIT_MS);
    return () => window.clearInterval(timer);
  }, [reading, poll]);
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
      const { outdated = [], ...saved } = await api.saveProfile({ ...profile, services, features: profile.features.map((f) => f.trim()).filter(Boolean) });
      setProfile(saved);
      await store.refresh();
      setStatus(savedText(outdated));
    } catch (e) {
      setStatus(message(e));
    } finally {
      setBusy(false);
    }
  };

  const readSite = async () => {
    setRead(null);
    setReadError(null);
    setStatus(null);
    try {
      await api.readSite(profile.website);
      setReading(true);
      await poll();
    } catch (e) {
      setReadError(message(e));
    }
  };
  const apply = (facts: SiteFact[]) => {
    if (read) setProfile(mergeFacts(profile, facts, read.url));
    setRead(null);
    setStatus("Перенёс в форму. Проверьте и нажмите «Сохранить»: в рекламу идут только сохранённые факты.");
  };

  return (
    <div className="set-form">
      <div className="set-site">
        <label><span className="ah-label">Сайт</span><input className="ah-input" value={profile.website} placeholder="salon.ru" onChange={(e) => set({ website: e.target.value })} /></label>
        <button type="button" className="ah-btn" disabled={!profile.website.trim() || reading} onClick={() => void readSite()}>Заполнить с сайта</button>
        {reading && <p className="set-hint" role="status">Читаю сайт. Обычно это минута-две, можно уйти с экрана и вернуться.</p>}
        {readError && <p role="alert" className="set-hint">{readError}</p>}
        {read && <SiteDraft key={read.id} read={read} onApply={apply} onClose={() => setRead(null)} />}
      </div>
      <label><span className="ah-label">Чем занимаетесь</span><input className="ah-input" value={profile.niche} placeholder="маникюр" onChange={(e) => set({ niche: e.target.value })} /></label>
      <label><span className="ah-label">Адрес</span><input className="ah-input" value={profile.address} onChange={(e) => set({ address: e.target.value })} /></label>
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
        </section>
        <section className="ah-panel set-section" aria-labelledby="set-display-title">
          <h2 id="set-display-title" className="ah-panel-title">Где и когда показывать</h2>
          <DisplayForm api={api} store={store as LiveStore} />
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
