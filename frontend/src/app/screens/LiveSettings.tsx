// Настройки на настоящих данных: профиль, уведомления, кабинет Яндекса и выход.
// Правка профиля не трогает площадку, поэтому идёт прямо в API, а не через журнал.
import { useCallback, useEffect, useRef, useState } from "react";
import type { AdAccount, Api, Business, Channels as ChannelsData, Display, Photos as PhotosData, Profile, SiteImage, SiteFact, SiteRead, TagCheckView } from "../../api";
import { rub } from "../data";
import { MICROS_PER_RUBLE } from "../money";
import * as v from "valibot";
import { PriceSchema, planProblem } from "../schemas";
import { isAbort } from "../polling";
import { liveApi, savedText, LiveStore } from "../live";
import { useStore } from "../store";
import type { Store } from "../store";
import { isCityFact } from "../draft";
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

function MetricaSettings({ api }: { api: Api }) {
  const [view, setView] = useState<TagCheckView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setView(await api.tagCheck());
      setError("");
    } catch (e) {
      setError(message(e));
    }
  }, [api]);

  useEffect(() => { void load(); }, [load]);
  const check = view?.check && view.check.url === view.site ? view.check : null;
  const waiting = check?.status === "queued" || check?.status === "running";
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => { void load(); }, WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [waiting, load, view]);

  const run = async () => {
    if (busy) return;
    if (!view) {
      await load();
      return;
    }
    if (!view.site) return;
    setBusy(true);
    setError("");
    try {
      setView(await api.requestTagCheck());
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  let status = "Загружаю состояние проверки…";
  if (view && !view.site) status = "Добавьте адрес сайта в профиле бизнеса, чтобы проверить установленный код. Запустить рекламу можно и без Метрики.";
  else if (waiting) status = "Проверяю сайт. Обычно это занимает до минуты.";
  else if (check?.status === "found") status = `Код найден, счётчик ${check.counters.join(", ")}.`;
  else if (check?.status === "missing") status = "Код Метрики на главной странице не найден. Если он установлен через Google Tag Manager, эта проверка его не увидит.";
  else if (check?.status === "failed") status = "Сайт не открылся при проверке. Попробуйте ещё раз позже.";
  else if (view?.site) status = "Код ещё не проверяли. Проверка необязательна для запуска рекламы.";

  return (
    <section className="ah-panel set-section" aria-labelledby="set-metrica-title">
      <h3 id="set-metrica-title" className="ah-panel-title">Код статистики на сайте</h3>
      <p className="set-hint">Проверю, установлен ли код Метрики. Без него реклама запустится, но обращения с сайта могут не учитываться.</p>
      <p className="set-hint">{status}</p>
      {error && <p className="ah-error" role="alert">{error}</p>}
      <div className="set-actions">
        <button type="button" className="ah-btn" disabled={(view !== null && !view.site) || busy || waiting} onClick={() => void run()}>
          {busy || waiting ? "Проверяю…" : error ? "Попробовать снова" : check ? "Проверить снова" : "Проверить установку"}
        </button>
      </div>
    </section>
  );
}

/** До запуска периода нет: показываем план из мастера. Лимиты в Яндексе из него ставятся только при запуске. */
function PlanForm({ api, businesses }: { api: Api; businesses?: Business[] }) {
  // Режим и общая сумма хранятся на сервере. Сумму не придумываем: только то, что владелец сам задал раньше.
  const [mode, setMode] = useState<"separate" | "shared">("separate");
  const [draft, setDraft] = useState("");
  const [sharedDraft, setSharedDraft] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const picked = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    const init = { signal: controller.signal };
    api.budgetPlan(init).then((p) => setDraft(p.rubles ? String(p.rubles) : ""), (e: unknown) => { if (!isAbort(e)) setStatus(message(e)); });
    api.budgetPortfolio(init).then((p) => {
      if (!picked.current) setMode(p.mode);
      if (p.rubles) setSharedDraft((now) => now || String(p.rubles));
    }, (e: unknown) => { if (!isAbort(e)) setStatus(message(e)); });
    return () => controller.abort();
  }, [api]);

  const count = Math.max(1, businesses?.length ?? 1);
  const sharedNum = Number(sharedDraft) || 0;

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    if (mode === "shared") {
      // Сервер делит сумму между всеми бизнесами и пишет доли одной операцией: либо у всех, либо ни у кого.
      api.saveBudgetPortfolio({ mode: "shared", rubles: sharedNum }).then(
        (saved) => {
          setStatus(`Сохранил общий бюджет: ${rub(sharedNum)} в месяц на ${count} ${count === 1 ? "бизнес" : count < 5 ? "бизнеса" : "бизнесов"}. Доли записаны для каждого бизнеса.`);
          api.budgetPlan().then((p) => setDraft(p.rubles ? String(p.rubles) : ""), () => undefined);
          return saved;
        },
        (err: unknown) => setStatus(message(err)),
      ).finally(() => setBusy(false));
    } else {
      const amount = Number(draft);
      const bad = planProblem(amount);
      if (bad) { setBusy(false); setStatus(bad); return; }
      api.saveBudgetPlan(amount).then(
        (p) => api.saveBudgetPortfolio({ mode: "separate" }).then(
          () => setStatus(`Сохранил план: ${rub(p.rubles ?? amount)} в месяц. Лимиты в Яндексе поставлю при запуске рекламы.`),
        ),
      ).catch((err: unknown) => setStatus(message(err))).finally(() => setBusy(false));
    }
  };

  return (
    <form className="set-form" aria-label="План бюджета" onSubmit={save}>
      <span className="ah-label">Режим распределения бюджета</span>
      <div className="ah-chips" role="group" aria-label="Режим бюджета" style={{ marginBottom: 12 }}>
        <button
          type="button"
          className="ah-chip"
          aria-pressed={mode === "separate"}
          onClick={() => { picked.current = true; setStatus(""); setMode("separate"); }}
        >
          Раздельный на каждый бизнес
        </button>
        <button
          type="button"
          className="ah-chip"
          aria-pressed={mode === "shared"}
          onClick={() => { picked.current = true; setStatus(""); setMode("shared"); }}
        >
          Общий на все бизнесы
        </button>
      </div>

      {mode === "separate" ? (
        <>
          <label>
            <span className="ah-label">План на запуск, ₽ в месяц</span>
            <input
              className="ah-input"
              inputMode="numeric"
              value={draft}
              onChange={(e) => { setStatus(""); setDraft(e.target.value.replace(/\D/g, "").slice(0, 9)); }}
            />
          </label>
          <p className="set-hint">Реклама ещё не запущена, денег не тратим. Лимиты в Яндексе поставлю при запуске, на эту сумму.</p>
        </>
      ) : (
        <>
          <label>
            <span className="ah-label">Общий бюджет на все бизнесы, ₽ в месяц</span>
            <input
              className="ah-input"
              inputMode="numeric"
              value={sharedDraft}
              onChange={(e) => { setStatus(""); setSharedDraft(e.target.value.replace(/\D/g, "").slice(0, 9)); }}
            />
          </label>
          <p className="set-hint">
            {sharedNum > 0 ? <>Единый лимит {rub(sharedNum)} в месяц делится между {count} {count === 1 ? "бизнесом" : "бизнесами"} (примерно по {rub(Math.floor(sharedNum / count))} на бизнес).</> : `Укажите общую сумму: она поделится между ${count} ${count === 1 ? "бизнесом" : "бизнесами"}.`}
          </p>
        </>
      )}

      {status && <p className="set-hint" role="status">{status}</p>}
      <div className="set-actions">
        <button type="submit" className="ah-btn" disabled={busy || (mode === "separate" ? !draft : !sharedDraft)}>
          Сохранить план
        </button>
      </div>
    </form>
  );
}

function BudgetForm({ store }: { store: LiveStore }) {
  const s = useStore(store, (x) => ({ live: x.live, businesses: x.businesses }));
  const live = s.live!;
  const [mode, setMode] = useState<"separate" | "shared">("separate");
  const [draft, setDraft] = useState(live.budget > 0 ? String(live.budget) : "");
  const [sharedDraft, setSharedDraft] = useState("");
  const [savedMode, setSavedMode] = useState<"separate" | "shared">("separate");
  const [asking, setAsking] = useState(false);
  const picked = useRef(false);
  const pick = (next: "separate" | "shared") => { picked.current = true; setAsking(false); setMode(next); };
  // Режим и общая сумма хранятся на сервере, а не в браузере.
  useEffect(() => {
    const controller = new AbortController();
    store.api.budgetPortfolio({ signal: controller.signal }).then((p) => {
      // Поздний ответ не перебивает выбор, который владелец уже сделал.
      if (!picked.current) setMode(p.mode);
      setSavedMode(p.mode);
      if (p.rubles) setSharedDraft((now) => now || String(p.rubles));
    }, (e: unknown) => { if (!isAbort(e)) store.set({ notice: message(e) }); });
    return () => controller.abort();
  }, [store]);
  const amount = Number(draft);
  const sharedAmount = Number(sharedDraft);
  const spent = live.spent ?? 0;
  const count = s.businesses?.length || 1;
  const perSite = Math.floor(sharedAmount / count);
  const problem = mode === "separate"
    ? (!draft ? "Укажите сумму." : amount < spent ? `В этом месяце уже потрачено ${rub(spent)}, бюджет не может быть меньше.` : null)
    : (!sharedDraft ? "Укажите сумму." : sharedAmount < spent ? `В этом месяце уже потрачено ${rub(spent)}, общий бюджет не может быть меньше.` : null);
  const lower = amount < live.budget;
  if (live.budget <= 0) return <PlanForm api={store.api} businesses={s.businesses} />;
  return (
    <form className="set-form" aria-label="Бюджет месяца" onSubmit={(e) => { e.preventDefault(); if (!problem && (mode === "shared" || amount !== live.budget)) setAsking(true); }}>
      <div className="ah-chips" style={{ marginBottom: 12 }}>
        <button
          type="button"
          className="ah-chip"
          aria-pressed={mode === "separate"}
          onClick={() => pick("separate")}
        >
          Раздельный на каждый бизнес
        </button>
        <button
          type="button"
          className="ah-chip"
          aria-pressed={mode === "shared"}
          onClick={() => pick("shared")}
        >
          Общий на все бизнесы
        </button>
      </div>

      {mode === "separate" ? (
        <>
          <label>
            <span className="ah-label">Бюджет в месяц, ₽</span>
            <input className="ah-input" inputMode="numeric" value={draft} onChange={(e) => { setAsking(false); setDraft(e.target.value.replace(/\D/g, "").slice(0, 9)); }} />
          </label>
          <p className="set-hint">Сейчас {rub(live.budget)}, потрачено {rub(spent)}. Яндекс не потратит больше: лимиты стоят в самом кабинете.</p>
        </>
      ) : (
        <>
          <label>
            <span className="ah-label">Общий бюджет на все бизнесы, ₽ в месяц</span>
            <input className="ah-input" inputMode="numeric" value={sharedDraft} onChange={(e) => { setAsking(false); setSharedDraft(e.target.value.replace(/\D/g, "").slice(0, 9)); }} />
          </label>
          <p className="set-hint">
            Единый лимит {rub(sharedAmount)} в месяц делится между {count} {count === 1 ? "бизнесом" : "бизнесами"} (примерно по {rub(perSite)} на бизнес). Сначала снижу бюджет там, где он уменьшается, потом подниму там, где растёт: сумма лимитов не выйдет за большую из старой и новой.
          </p>
        </>
      )}

      {problem && <p className="ah-error" role="alert">{problem}</p>}
      {asking ? (
        <div className="set-confirm" role="group" aria-label="Подтверждение бюджета">
          <p>
            {mode === "shared"
              ? `Поставить общий бюджет ${rub(sharedAmount)} на все бизнесы? Сумма поделится между ${count} ${count === 1 ? "бизнесом" : "бизнесами"}, у запущенных лимиты сменятся по очереди. Если на каком-то бизнесе не получится, остановлюсь и скажу, что уже изменено.`
              : `Поставить ${rub(amount)} в месяц вместо ${rub(live.budget)}? ${lower ? "Сначала снижу лимиты в Яндексе, потом сменю бюджет: так расход не выйдет за новую сумму ни в какой момент." : "Лимиты в Яндексе не изменятся сразу: автопилот поднимает их по правилам, понемногу."}`}
          </p>
          <div className="set-actions">
            <button type="button" className="ah-btn ah-btn-primary" onClick={() => {
              setAsking(false);
              if (mode === "shared") {
                store.applySharedBudget(sharedAmount).then(
                  (text) => { setSavedMode("shared"); store.set({ notice: text }); },
                  (e: unknown) => store.set({ notice: message(e) }),
                );
              } else {
                store.run({ action: "setting", value: ["budget", amount] });
                if (savedMode === "shared") {
                  store.api.saveBudgetPortfolio({ mode: "separate" }).then(() => setSavedMode("separate"), () => undefined);
                }
              }
            }}>Применить</button>
            <button type="button" className="ah-btn" onClick={() => { setAsking(false); setDraft(String(live.budget)); }}>Не менять</button>
          </div>
        </div>
      ) : (
        <div className="set-actions"><button type="submit" className="ah-btn" disabled={!!problem || (mode === "separate" && amount === live.budget)}>Изменить бюджет</button></div>
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
  // Первый ответ не затирает то, что владелец уже выбрал; после сохранения форму перечитываем всегда.
  const loaded = useRef(false);
  const load = useCallback(async (force = true) => {
    const d = await api.display();
    if (!force && loaded.current) return;
    loaded.current = true;
    setDisplay(d);
    setRegions(d.regions ?? []);
    if (d.schedule) { setDays(d.schedule.days); setHours([d.schedule.start, d.schedule.end]); }
    setGoal(d.goal ? String(d.goal) : "");
  }, [api]);
  useEffect(() => { load(false).catch((e: unknown) => setStatus(message(e))); }, [load]);
  if (!display) return <p className="ah-muted">{status ?? "Загружаю…"}</p>;

  const names = (ids: number[]) => display.catalog.filter((r) => ids.includes(r.id)).map((r) => r.name).join(", ");
  const weeklyHours = days.length * Math.max(0, hours[1] - hours[0]);
  const scheduleProblem = !days.length ? "Выберите хотя бы один день." : hours[0] >= hours[1] ? "Начало должно быть раньше конца." : weeklyHours < display.min_weekly_hours ? `Яндекс не принимает меньше ${display.min_weekly_hours} часов показа в неделю, сейчас ${weeklyHours}.` : null;
  const sorted = (arr: number[]) => [...arr].sort((a, b) => a - b).join();
  const sameSchedule = !!display.schedule && sorted(display.schedule.days) === sorted(days) && display.schedule.start === hours[0] && display.schedule.end === hours[1];
  const sameRegions = !!display.regions && sorted(display.regions) === sorted(regions);
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
      <div role="group" aria-label="Где показывать">
        <div className="ah-label">Где показывать</div>
        {noCampaigns ? (
          <p className="set-hint">Кампаний ещё нет. Этот список не задаёт город новому запуску: он берётся из поля «Адрес» в профиле. После запуска здесь можно изменить регионы уже созданных кампаний.</p>
        ) : (
          <>
            <p className="set-hint">Сейчас: {display.regions_text ?? "у направлений разный регион или Яндекс его ещё не передал"}.</p>
            <p className="set-hint">Отметьте нужные регионы и нажмите «Изменить регион». Изменение коснётся всех кампаний бизнеса; отменить можно в истории.</p>
            <div className="set-options">
              {display.catalog.map((r) => (
                <button key={r.id} type="button" className="ah-chip" aria-pressed={regions.includes(r.id)} onClick={() => { setStatus(null); setRegions(toggle(regions, r.id)); }}>{r.name}</button>
              ))}
            </div>
            {!regions.length && <p className="ah-error" role="alert">Выберите хотя бы один регион.</p>}
            <div className="set-actions">
              <button type="button" className="ah-btn" disabled={!regions.length || sameRegions || busy} onClick={() => setAsk({ text: `Показывать рекламу: ${names(regions)}${display.regions_text ? ` вместо «${display.regions_text}»` : ""}? Это изменится в Яндексе для всех направлений, отменить можно в истории.`, apply: async () => { await api.setRegions(regions, crypto.randomUUID()); return `Регион показов теперь: ${names(regions)}. Вы попросили. Отменить можно в истории.`; } })}>Изменить регион</button>
            </div>
          </>
        )}
      </div>
      <div role="group" aria-label="Когда показывать">
        <div className="ah-label">Когда показывать</div>
        {!noCampaigns && <p className="set-hint">Сейчас: {display.schedule_text ?? "у направлений разное время или Яндекс его ещё не передал"}.</p>}
        <div className="set-options">
          {DAY_NAMES.map((name, d) => (
            <button key={name} type="button" className="ah-chip" aria-pressed={days.includes(d)} onClick={() => { setStatus(null); setDays(toggle(days, d).sort((a, b) => a - b)); }}>{name}</button>
          ))}
        </div>
        <div className="set-time">
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
        {goal !== "" && goalValue < 1 && <p className="ah-error" role="alert">Цена должна быть больше нуля.</p>}
        <p className="set-hint">{display.goal ? `Сейчас ${rub(display.goal)}.` : "Пока не задана."} В Яндексе это ничего не меняет. Если обращение выходит заметно дороже, автопилот отключает такое объявление.</p>
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
  const cityFacts = facts.filter((fact) => fact.field === "feature" && isCityFact(fact.value));
  const cityFact = cityFacts.length === 1 ? cityFacts[0] : undefined;
  for (const fact of facts) {
    if (fact.field === "niche") niche = fact.value;
    else if (fact.field === "address") address = fact.value;
    else if (fact.field === "service") {
      const i = services.findIndex((x) => x.name.trim().toLowerCase() === fact.value.trim().toLowerCase());
      const same = services[i];
      // Отмеченная цена — выбор владельца: она заменяет прежнюю.
      if (same) services[i] = { ...same, price: fact.price ?? same.price };
      else if (services.length < MAX_SERVICES) services.push({ name: fact.value, price: fact.price });
    } else if (!(cityFact && fact === cityFact) && !features.some((f) => f.trim().toLowerCase() === fact.value.trim().toLowerCase()) && features.length < MAX_FEATURES) {
      features.push(fact.value);
    }
  }
  if (cityFact && !address.toLocaleLowerCase().includes(cityFact.value.toLocaleLowerCase())) {
    address = [address, cityFact.value].filter(Boolean).join(", ");
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

const PRICE_TEXT = /^[\d\s]*$/u;
const PRICE_PROBLEM = "Цена — положительное число, без минуса и букв.";

/** Цена в названии услуги: «Стрижка 500р», «маникюр 1 200 ₽». */
const PRICE_IN_NAME = /\d[\d\s]*\s*(₽|р\.?|руб)/iu;

/** Что не так с услугами, словами владельца; null, если можно сохранять. */
export function servicesProblem(services: Profile["services"]): string | null {
  for (const x of services) {
    if (!x.name.trim()) continue;
    if (!v.safeParse(PriceSchema, x.price).success) return `Цена услуги «${x.name.trim()}» должна быть больше нуля.`;
    if (PRICE_IN_NAME.test(x.name)) return `В названии «${x.name.trim()}» есть цена. Уберите её из названия и впишите в поле цены: в рекламу цена идёт только оттуда.`;
  }
  return null;
}

const freshIds = (n: number): string[] => Array.from({ length: n }, () => crypto.randomUUID());

function ProfileForm({ api, store }: { api: Api; store: LiveStore }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [featuresText, setFeaturesText] = useState<string | null>(null);
  // Цена как её набрал владелец: «-5» показываем и не сохраняем, а не превращаем в 5.
  // Ключ строки услуги не зависит от места в списке: удаление строки не сбивает чужие черновики цен.
  const [priceText, setPriceText] = useState<Record<string, string>>({});
  const [rowIds, setRowIds] = useState<string[]>([]);
  const originalProfile = useRef<Profile | null>(null);
  // Первый пришедший профиль и есть форма: поздний ответ не затирает то, что владелец уже ввёл.
  useEffect(() => {
    api.profile().then((loaded) => {
      setProfile((current) => {
        if (current) return current;
        setRowIds(freshIds(loaded.services.length));
        originalProfile.current = loaded;
        return loaded;
      });
    }, (e: unknown) => setStatus(message(e)));
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
  const dirty = JSON.stringify(profile) !== JSON.stringify(originalProfile.current) || Object.keys(priceText).length > 0;

  const set = (patch: Partial<Profile>) => {
    setProfile({ ...profile, ...patch });
    setStatus(null);
  };
  const service = (i: number, patch: Partial<Profile["services"][number]>) =>
    set({ services: profile.services.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const save = async () => {
    const problem = Object.values(priceText).some((t) => !PRICE_TEXT.test(t)) ? PRICE_PROBLEM : servicesProblem(profile.services);
    if (problem) return setStatus(problem);
    setBusy(true);
    try {
      const services = profile.services.filter((x) => x.name.trim());
      const { outdated = [], ...saved } = await api.saveProfile({ ...profile, services, features: profile.features.map((f) => f.trim()).filter(Boolean) });
      setProfile(saved);
      originalProfile.current = saved;
      setRowIds(freshIds(saved.services.length));
      setPriceText({});
      setFeaturesText(null);
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
    if (read) {
      const merged = mergeFacts(profile, facts, read.url);
      setProfile(merged);
      setRowIds(freshIds(merged.services.length));
      setPriceText({});
      setFeaturesText(null);
    }
    setRead(null);
    setStatus("Перенёс в форму. Проверьте и нажмите «Сохранить»: в рекламу идут только сохранённые факты.");
  };

  return (
    <div className="set-form set-profile-form">
      <div className="set-site">
        <label><span className="ah-label">Сайт</span><input className="ah-input" value={profile.website} placeholder="salon.ru" onChange={(e) => set({ website: e.target.value })} /></label>
        <button type="button" className="ah-btn" disabled={!profile.website.trim() || reading} onClick={() => void readSite()}>Заполнить с сайта</button>
        {reading && <p className="set-hint" role="status">Читаю сайт. Обычно это минута-две, можно уйти с экрана и вернуться.</p>}
        {readError && <p role="alert" className="set-hint">{readError}</p>}
        {read && <SiteDraft key={read.id} read={read} onApply={apply} onClose={() => setRead(null)} />}
      </div>
      <label><span className="ah-label">Чем занимаетесь</span><input className="ah-input" value={profile.niche} placeholder="маникюр" onChange={(e) => set({ niche: e.target.value })} /></label>
      <label><span className="ah-label">Адрес</span><input className="ah-input" value={profile.address} onChange={(e) => set({ address: e.target.value })} /></label>
      <p className="set-hint">Город для нового запуска берётся из этого поля. Укажите его в начале адреса, например: «Пермь, ул. Комсомольский проспект, 71», затем нажмите «Сохранить» внизу.</p>
      <fieldset className="set-services">
        <legend className="ah-label">Услуги и цены{profile.services.length > 0 ? ` (${profile.services.length})` : ""}</legend>
        <div className="set-service-list">
        {profile.services.map((x, i) => (
          <div key={rowIds[i] ?? i} className="set-service">
            <input className="ah-input" aria-label={`Услуга ${i + 1}`} value={x.name} onChange={(e) => service(i, { name: e.target.value })} />
            <input className="ah-input set-price" aria-label={`Цена услуги ${i + 1}, ₽`} inputMode="numeric" placeholder="₽" value={priceText[rowIds[i] ?? ""] ?? x.price ?? ""} onChange={(e) => {
              // Минус и буквы не отбрасываем молча: текст остаётся в поле, сохранить его нельзя.
              const raw = e.target.value.slice(0, 10);
              setPriceText({ ...priceText, [rowIds[i] ?? ""]: raw });
              const ok = PRICE_TEXT.test(raw);
              const digits = raw.replace(/\s/g, "");
              service(i, { price: ok && digits ? Number(digits) : null });
              if (!ok) setStatus(PRICE_PROBLEM);
            }} />
            <button type="button" className="ah-link" aria-label={`Убрать услугу ${i + 1}`} onClick={() => {
              const { [rowIds[i] ?? ""]: _gone, ...rest } = priceText;
              setPriceText(rest);
              setRowIds(rowIds.filter((_, j) => j !== i));
              set({ services: profile.services.filter((_, j) => j !== i) });
            }}>Убрать</button>
          </div>
        ))}
        </div>
        <button type="button" className="ah-btn" onClick={() => { setRowIds([...rowIds, crypto.randomUUID()]); set({ services: [...profile.services, { name: "", price: null }] }); }}>Добавить услугу</button>
      </fieldset>
      <label>
        <span className="ah-label">Чем вы отличаетесь, по одному факту в строке</span>
        <textarea
          className="ah-input set-textarea"
          rows={4}
          value={featuresText ?? profile.features.join("\n")}
          onChange={(e) => {
            const val = e.target.value;
            setFeaturesText(val);
            set({ features: val.split("\n") });
          }}
        />
      </label>
      <div className={dirty ? "set-actions set-profile-actions set-profile-actions-dirty" : "set-actions set-profile-actions"}>
        <button type="button" className="ah-btn ah-btn-primary" disabled={busy || !dirty} onClick={save}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        <span role="status" className="set-hint">{status ?? (dirty ? "Есть несохранённые изменения." : "Все изменения сохранены.")}</span>
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
    ? "Telegram пока недоступен, тревоги приходят на почту. Бот пока не настроен на сервере."
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

/** Остаток на счёте в Директе: читается из Яндекса при открытии, сохраняется в базе данных. */
function Balance({
  api,
  login,
  initialBalance,
  onBalance,
}: {
  api: Api;
  login: string;
  initialBalance?: number | null;
  onBalance?: (rubles: number) => void;
}) {
  const [text, setText] = useState(() =>
    initialBalance !== null && initialBalance !== undefined
      ? `На счёте в Директе: ${rub(Math.round(initialBalance / MICROS_PER_RUBLE))}`
      : "Смотрю остаток в Директе…"
  );
  useEffect(() => {
    api.balance(login).then(
      (b) => {
        if (b.balance_micros !== null) {
          const rubles = Math.round(b.balance_micros / MICROS_PER_RUBLE);
          setText(`На счёте в Директе: ${rub(rubles)}`);
          onBalance?.(rubles);
        } else {
          setText(b.problem ?? "Остаток сейчас недоступен.");
        }
      },
      (e: unknown) => setText(message(e)),
    );
  }, [api, login, onBalance]);
  return <p className="set-hint">{text}</p>;
}

/** Запасной путь, пока Яндекс не одобрил наше приложение: токен берётся у Яндекса и вставляется сюда. */
function ManualToken({ api, onSaved, onChecked }: { api: Api; onSaved: (account: AdAccount) => void; onChecked: (available: boolean) => void }) {
  const [link, setLink] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [login, setLogin] = useState("");
  const [askLogin, setAskLogin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.manualTokenLink().then((r) => { setLink(r.url); onChecked(true); }, () => { setLink(null); onChecked(false); });
  }, [api, onChecked]);
  if (!link) return null;
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.saveManualToken(token.trim(), login.trim() || undefined));
      setToken("");
      setLogin("");
      setAskLogin(false);
    } catch (e) {
      // Если Директ не назвал логин, просим вписать его: токен остаётся в поле.
      setAskLogin(/логин/i.test(message(e)));
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="set-manual">
      <p className="set-hint">Нажмите кнопку, разрешите доступ в Яндексе, скопируйте токен с экрана и вставьте сюда.</p>
      <div><button type="button" className="ah-btn" aria-label="Получить код доступа к Директу" onClick={() => window.open(link, "_blank", "noopener")}>Получить код доступа к Директу</button></div>
      <label className="set-field"><span className="ah-label">Токен</span><input className="ah-input" type="password" value={token} placeholder="Вставьте токен" onChange={(e) => setToken(e.target.value)} autoComplete="off" /></label>
      {askLogin && <label className="set-field"><span className="ah-label">Логин в Директе</span><input className="ah-input" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="off" /></label>}
      <p className="set-hint">Токен хранится в зашифрованном виде и действует около года: потом понадобится получить новый.</p>
      <div><button type="button" className="ah-btn ah-btn-primary" disabled={busy || !token.trim()} onClick={save}>Сохранить токен</button></div>
      {error && <p className="ah-error" role="alert">{error}</p>}
    </div>
  );
}

function Accounts({ api, store }: { api: Api; store?: Store }) {
  const [accounts, setAccounts] = useState<AdAccount[] | null>(null);
  const [available, setAvailable] = useState<AdAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Показываем один способ входа: токен вручную, а если он недоступен, вход через Яндекс.
  const [manual, setManual] = useState<boolean | null>(null);
  useEffect(() => {
    api.accounts().then(
      (accs) => {
        setAccounts(accs);
        const hasWorking = accs.some((a) => a.state === "connected" && (a as { status?: string }).status !== "needs_reconnect");
        store?.set({ yandex: hasWorking });
      },
      (e: unknown) => setError(message(e)),
    );
    api.availableAccounts?.().then(setAvailable, (e: unknown) => setError(message(e)));
  }, [api, store]);
  const onBalance = useCallback(
    (rubles: number) => {
      if (store && "live" in store.state && store.state.live) {
        store.set({
          live: {
            ...store.state.live,
            balance: rubles,
          },
        });
      }
    },
    [store],
  );
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
  const attach = async (login: string) => {
    setBusy(true);
    setError(null);
    try {
      const saved = await api.attachAccount(login);
      setAccounts((all) => [...(all ?? []).filter((item) => item.login !== saved.login), saved]);
      setAvailable((all) => all.filter((item) => item.login !== saved.login));
      store?.set({ yandex: true });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const working = accounts?.some((a) => a.state === "connected" && (a as { status?: string }).status !== "needs_reconnect");
  const reconnectNeeded = accounts?.some(
    (a) => a.state === "needs_reconnect" || (a as { status?: string }).status === "needs_reconnect"
  );
  return (
    <div className="set-rows">
      {accounts === null && !error && <p className="ah-muted">Загружаю…</p>}
      {accounts?.length === 0 && <p className="set-hint">Кабинет ещё не подключён: без него реклама не запустится. Деньги списываются с вашего счёта в Яндексе, мы их не держим.</p>}
      {reconnectNeeded && (
        <div className="ah-error" role="alert" style={{ marginBottom: 12 }}>
          Связь с кабинетом Яндекса прервалась. {manual === false ? "Нажмите «Переподключить»" : "Получите новый токен и вставьте его ниже"}, чтобы обновить доступ без потери данных рекламных кампаний.
        </div>
      )}
      {accounts?.map((a) => (
        <div key={a.id} className="set-row">
          <div><strong>{a.login}</strong><p className="set-hint">Яндекс Директ</p>{a.state === "connected" && (a as { status?: string }).status !== "needs_reconnect" && <Balance api={api} login={a.login} initialBalance={a.last_balance_micros} onBalance={onBalance} />}</div>
          <span className={a.state === "connected" && (a as { status?: string }).status !== "needs_reconnect" ? "ah-tag ah-tag-good" : "ah-tag ah-tag-bad"}>{ACCOUNT_STATE[a.state] ?? a.state}</span>
        </div>
      ))}
      {available.length > 0 && (
        <div className="set-section">
          <p className="set-hint">Кабинет уже подключён в другом вашем бизнесе. Баланс у него общий.</p>
          {available.map((account) => (
            <button
              key={account.login}
              type="button"
              className="ah-btn"
              disabled={busy}
              onClick={() => void attach(account.login)}
            >
              Подключить кабинет {account.login}
            </button>
          ))}
        </div>
      )}
      {accounts !== null && manual === false && (
        <div><button type="button" className={working ? "ah-btn" : "ah-btn ah-btn-primary"} disabled={busy} onClick={connect}>{reconnectNeeded ? "Переподключить" : (accounts.length ? "Подключить заново" : "Подключить кабинет")}</button></div>
      )}
      {accounts !== null && (
        <ManualToken api={api} onChecked={setManual} onSaved={(saved) => {
          setAccounts((all) => [...(all ?? []).filter((x) => x.login !== saved.login), saved]);
          store?.set({ yandex: true });
          if (saved.last_balance_micros != null && store && "live" in store.state && store.state.live) {
            store.set({
              live: {
                ...store.state.live,
                balance: Math.round(saved.last_balance_micros / MICROS_PER_RUBLE),
                account: saved.login,
              },
            });
          }
        }} />
      )}
      {error && <p className="ah-error" role="alert">{error}</p>}
    </div>
  );
}

/** Свои фото для объявлений. Яндекс показывает картинку рядом с текстом; без фото объявления идут как есть. */
const PHOTO_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
// Совпадает с подписью раздела; окончательно размер проверяет сервис.
const MIN_PHOTO_SIDE = 450;
const NO_CONSENT = "Сначала отметьте, что люди на фото согласны на рекламу или людей на фото нет.";

/** Короткая сторона картинки; null, если браузер не умеет её прочитать — тогда решит сервис. */
async function shortSide(file: Blob): Promise<number | null> {
  if (typeof createImageBitmap !== "function") return null;
  try {
    const bitmap = await createImageBitmap(file);
    const side = Math.min(bitmap.width, bitmap.height);
    bitmap.close();
    return side;
  } catch {
    return null;
  }
}

function Photos({ api }: { api: Api }) {
  const [data, setData] = useState<PhotosData | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const siteLookupStarted = useRef<Api | null>(null);
  const [consent, setConsent] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => setData(await api.photos()), [api]);
  useEffect(() => { load().catch((e: unknown) => setError(message(e))); }, [load]);
  const act = async (work: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      setStatus(await work());
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const refuse = (text: string) => {
    setStatus(null);
    setError(text);
  };
  const [siteUrl, setSiteUrl] = useState("");
  const [found, setFound] = useState<SiteImage[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  // Найти варианты автоматически по адресу профиля, но никогда не сохранять их
  // без явного выбора владельца и согласия на права/людей на снимках.
  useEffect(() => {
    if (!data || data.photos.length >= data.max || siteLookupStarted.current === api) return;
    siteLookupStarted.current = api;
    let active = true;
    void api.profile().then(async (profile) => {
      const website = profile.website.trim();
      if (!website || !active) return;
      setSiteUrl(website);
      setBusy(true);
      setStatus("Ищу подходящие фото на сайте…");
      setError(null);
      try {
        const result = await api.siteImages(website);
        if (!active) return;
        setFound(result.images);
        setChosen([]);
        setStatus(result.images.length
          ? `Нашёл на сайте ${result.images.length} фото. Выберите подходящие и подтвердите права на них.`
          : "Подходящих фото на сайте не нашёл. Можно продолжить без фото.");
      } catch (e) {
        if (active) setError(`Не удалось найти фото на сайте: ${message(e)}`);
      } finally {
        if (active) setBusy(false);
      }
    }, (e: unknown) => {
      if (active) setError(message(e));
    });
    return () => { active = false; };
  }, [api, data]);
  const lookUp = () => {
    if (!siteUrl.trim()) return refuse("Впишите адрес сайта.");
    void act(async () => {
      const res = await api.siteImages(siteUrl.trim());
      setFound(res.images);
      setChosen([]);
      return `Нашёл ${res.images.length} картинок. Отметьте те, что подходят для рекламы.`;
    });
  };
  const importChosen = () => {
    if (!consent) return refuse(NO_CONSENT);
    if (chosen.length === 0) return refuse("Отметьте хотя бы одну картинку.");
    void act(async () => {
      const res = await api.importSiteImages(chosen, true);
      setFound(null);
      setChosen([]);
      setConsent(false);
      return `Добавил фото: ${res.added}${res.skipped ? `, не подошло: ${res.skipped}` : ""}.`;
    });
  };
  const upload = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    // Каждый отказ называет причину и убирает прошлое «Фото добавил».
    if (!consent) return refuse(NO_CONSENT);
    if (!PHOTO_TYPES.includes(file.type)) return refuse("Нужна картинка JPEG, PNG, GIF или WebP.");
    const side = await shortSide(file);
    if (side !== null && side < MIN_PHOTO_SIDE) return refuse(`Фото меньше ${MIN_PHOTO_SIDE} точек по короткой стороне (сейчас ${side}). Возьмите фото крупнее.`);
    void act(async () => {
      await api.uploadPhoto(file, true);
      setConsent(false);
      return "Фото добавил. Поставлю его в новые объявления: Яндекс проверит его вместе с текстом.";
    });
  };
  if (!data) return <p className="ah-muted">{error ?? "Загружаю…"}</p>;
  const full = data.photos.length >= data.max;
  return (
    <div className="set-rows">
      <p className="set-hint">Фото необязательны: если своих не будет, сервис подберёт или создаст изображение сам. До {data.max} фото салона, работ или мастеров; квадрат или кадр 4:3, от 450 точек по короткой стороне.</p>
      {data.photos.length > 0 && (
        <ul className="set-photos" aria-label="Ваши фото">
          {data.photos.map((p, i) => (
            <li key={p.id} className="set-photo">
              <img src={p.url} alt={`Фото ${i + 1}`} width={p.width} height={p.height} loading="lazy" />
              <button type="button" className="ah-btn" disabled={busy} onClick={() => act(async () => { await api.archivePhoto(p.id); return "Фото убрал из подбора. Объявления, где оно уже стоит, работают дальше."; })}>Убрать фото {i + 1}</button>
            </li>
          ))}
        </ul>
      )}
      {full ? (
        <p className="set-hint">Уже {data.max} фото. Уберите одно, чтобы добавить новое.</p>
      ) : (
        <>
          <label className="set-check">
            <input type="checkbox" checked={consent} onChange={(e) => { setConsent(e.target.checked); setError(null); }} />
            <span>У меня есть права использовать фото; люди на нём согласны на рекламу или их нет</span>
          </label>
          <label className="set-field">
            <span className="ah-label">Адрес сайта, откуда взять фото</span>
            <input className="ah-input" type="url" value={siteUrl} placeholder="https://" onChange={(e) => setSiteUrl(e.target.value)} />
          </label>
          <div className="set-actions">
            <button type="button" className="ah-btn" disabled={busy} onClick={() => (consent ? picker.current?.click() : refuse(NO_CONSENT))}>{busy ? "Загружаю…" : "Добавить фото"}</button>
            <button type="button" className="ah-btn" disabled={busy} onClick={lookUp}>Взять фото с сайта</button>
            <input ref={picker} type="file" aria-label="Файл фото" accept={PHOTO_TYPES.join(",")} className="set-file" tabIndex={-1} onChange={(e) => { void upload(e.target.files); e.target.value = ""; }} />
          </div>
        </>
      )}
      {found && (
        <>
          <ul className="set-photos" aria-label="Картинки сайта">
            {found.map((img, i) => (
              <li key={img.url} className="set-photo">
                <img src={img.preview} alt={`Картинка сайта ${i + 1}`} loading="lazy" />
                <label className="set-check">
                  <input type="checkbox" checked={chosen.includes(img.url)} onChange={(e) => setChosen(e.target.checked ? [...chosen, img.url] : chosen.filter((u) => u !== img.url))} />
                  <span>Взять картинку {i + 1}</span>
                </label>
              </li>
            ))}
          </ul>
          <button type="button" className="ah-btn" disabled={busy} onClick={importChosen}>Добавить отмеченные</button>
        </>
      )}
      {status && <p className="set-hint" role="status">{status}</p>}
      {error && <p className="ah-error" role="alert">{error}</p>}
    </div>
  );
}

function DeleteBusiness({ store, businessId, businessName }: { store: Store; businessId: string; businessName: string }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      if (store instanceof LiveStore) {
        await store.deleteBusiness(businessId);
      } else {
        const remaining = (store.state.businesses ?? []).filter((b) => b.id !== businessId);
        store.set({ businesses: remaining });
      }
    } catch (e) {
      setError(message(e));
      setBusy(false);
      setAsking(false);
    }
  };

  return (
    <div className="set-danger">
      <p className="set-hint">Удалит бизнес «{businessName}» из сервиса. Кампании этого бизнеса будут остановлены и перенесены в архив. Другие ваши бизнесы в этом кабинете продолжат работу.</p>
      {asking ? (
        <div className="set-confirm" role="group" aria-label="Подтверждение удаления бизнеса" style={{ marginTop: 12 }}>
          <p>Удалить бизнес «{businessName}»? Это действие нельзя отменить.</p>
          <div className="set-actions" style={{ marginTop: 12 }}>
            <button type="button" className="ah-btn ah-btn-danger" disabled={busy} onClick={remove}>
              {busy ? "Удаляю…" : "Да, удалить бизнес"}
            </button>
            <button type="button" className="ah-btn" disabled={busy} onClick={() => setAsking(false)}>
              Отмена
            </button>
          </div>
        </div>
      ) : (
        <div className="set-actions" style={{ marginTop: 12 }}>
          <button type="button" className="ah-btn ah-btn-danger" onClick={() => setAsking(true)}>
            Удалить бизнес
          </button>
        </div>
      )}
      {error && <p className="ah-error" role="alert" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  );
}

// У каждого сайта свои данные: формы настроек пересоздаются при смене сайта, чтобы не сохранить данные одного в другой.
export function LiveSettings({ store }: { store: Store }) {
  // Подписка на магазин: смена сайта меняет API, и экран должен это увидеть.
  const activeBusinessId = useStore(store, (s) => s.activeBusinessId);
  const api = liveApi(store)!;
  return <SiteSettings key={activeBusinessId ?? "none"} store={store} api={api} />;
}

function SiteSettings({ store, api }: { store: Store; api: Api }) {
  const s = useStore(store, (x) => ({ activeBusinessId: x.activeBusinessId, businesses: x.businesses, live: x.live }));
  const businessName = s.businesses?.find((b) => b.id === s.activeBusinessId)?.name ?? s.live?.name ?? "этот бизнес";
  const [leaving, setLeaving] = useState<string | null>(null);
  const logout = async () => {
    setLeaving("Выхожу…");
    try {
      await api.logout();
      try {
        document.cookie = "ah_guest=1; path=/";
      } catch {
        // ignore in environments without cookie access
      }
      window.location.assign("/");
    } catch (e) {
      setLeaving(message(e));
    }
  };
  const live = s.live;
  const go = (id: string) => document.getElementById(id)?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  return (
    <div className="set">
      <header className="set-head">
        <div>
          <h1 className="set-title">Настройки</h1>
          <p className="set-sub">{businessName}</p>
        </div>
        <button type="button" className="ah-btn" disabled={leaving === "Выхожу…"} onClick={logout}>Выйти</button>
      </header>
      {leaving && leaving !== "Выхожу…" && <p className="ah-error" role="alert">{leaving}</p>}
      <ul className="set-summary" aria-label="Коротко">
        {live && live.budget > 0 && <li><span>Бюджет в месяц</span><strong>{rub(live.budget)}</strong><em>потрачено {rub(live.spent ?? 0)}</em></li>}
        {live?.account && <li><span>Кабинет Яндекса</span><strong>{live.account}</strong>{live.balance != null && <em>на счёте {rub(live.balance)}</em>}</li>}
      </ul>
      <nav className="set-nav" aria-label="Разделы настроек">
        {SET_GROUPS.map((g) => <button key={g.id} type="button" className="ah-chip" onClick={() => go(g.id)}>{g.title}</button>)}
      </nav>
      <div className="set-body">
        <section id="set-money" className="set-group" aria-labelledby="set-money-h">
          <h2 id="set-money-h" className="set-group-title">Деньги</h2>
          <p className="set-group-sub">Сколько тратить. Яндекс не потратит больше: лимиты стоят в самом кабинете.</p>
          <section className="ah-panel set-section" aria-labelledby="set-title">
            <h3 id="set-title" className="ah-panel-title">Бюджет</h3>
            <BudgetForm store={store as LiveStore} />
          </section>
        </section>
        <section id="set-show" className="set-group" aria-labelledby="set-show-h">
          <h2 id="set-show-h" className="set-group-title">Показы</h2>
          <p className="set-group-sub">Где и когда видят рекламу. Изменения применяются в Яндексе после подтверждения.</p>
          <section className="ah-panel set-section" aria-labelledby="set-display-title">
            <h3 id="set-display-title" className="ah-panel-title">Где и когда показывать</h3>
            <DisplayForm api={api} store={store as LiveStore} />
          </section>
        </section>
        <section id="set-about" className="set-group" aria-labelledby="set-about-h">
          <h2 id="set-about-h" className="set-group-title">О бизнесе</h2>
          <p className="set-group-sub">В рекламу попадают только эти факты и фото. Чего здесь нет, того в объявлениях не будет.</p>
          <section className="ah-panel set-section" aria-labelledby="set-profile-title">
            <h3 id="set-profile-title" className="ah-panel-title">Профиль бизнеса</h3>
            <ProfileForm api={api} store={store as LiveStore} />
          </section>
          <section className="ah-panel set-section" aria-labelledby="set-photos-title">
            <h3 id="set-photos-title" className="ah-panel-title">Фото для объявлений</h3>
            <Photos api={api} />
          </section>
        </section>
        <section id="set-links" className="set-group" aria-labelledby="set-links-h">
          <h2 id="set-links-h" className="set-group-title">Подключения</h2>
          <p className="set-group-sub">Откуда сервис берёт данные и куда пишет, если что-то пошло не так.</p>
          <section className="ah-panel set-section" aria-labelledby="set-account-title">
            <h3 id="set-account-title" className="ah-panel-title">Кабинет Яндекса</h3>
            <Accounts api={api} store={store} />
          </section>
          <MetricaSettings api={api} />
          <section className="ah-panel set-section" aria-labelledby="set-channels-title">
            <h3 id="set-channels-title" className="ah-panel-title">Уведомления</h3>
            <p className="set-hint">Напишу, если показы остановлены или с деньгами что-то не так.</p>
            <Channels api={api} />
          </section>
        </section>
        <section id="set-danger" className="set-group" aria-labelledby="set-delete-title">
          <section className="ah-panel set-section set-danger">
            <h3 id="set-delete-title" className="ah-panel-title">Удаление бизнеса</h3>
            <DeleteBusiness store={store} businessId={s.activeBusinessId ?? ""} businessName={businessName} />
          </section>
        </section>
      </div>
    </div>
  );
}

const SET_GROUPS = [
  { id: "set-money", title: "Деньги" },
  { id: "set-show", title: "Показы" },
  { id: "set-about", title: "О бизнесе" },
  { id: "set-links", title: "Подключения" },
];
