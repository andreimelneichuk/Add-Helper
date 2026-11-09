import { useRef, useSyncExternalStore } from "react";
import { ApiError } from "../api";
import type { AdDraftProfile, AdSet, AdvertiserKind, Api, ChatCommand, Me, Business, SiteFact, SiteRead, UndoPreview, WizardAd } from "../api";
import { pollUntil } from "./polling";
import { clearDraft, clearStartReading, clearWizard, draftProfile, loadDraft, loadWizard, saveDraft, saveStartReading, saveStartSite, transferDraft } from "./draft";
import type { TransferDeps } from "./draft";
import { leadsWord, MIN_BUDGET, rub, STEPS } from "./data";
import { fixtures } from "./fixtures";
import type { Ad, Day, Fact, HistoryEntry, HistoryKind, Live, Period, PeriodStats, SettingKey, Situation, View } from "./data";

export type Who = "me" | "ai" | "note";
export type Message = { id: number; who: Who; text: string; link?: { label: string; view: View } };
type Pending =
  | { action: "pause" }
  | { action: "setting"; value: [SettingKey, string | number] }
  // whole: отменить просьбу по всем направлениям, а не одно.
  | { action: "undo"; value: string; whole?: boolean }
  | { action: "price"; value: number }
  | { action: "ad"; value: string }
  | { action: "adPublish"; value: string; grouping: "services" | "general" }
  | { action: "adSave"; value: Ad }
  | { action: "adArchive"; value: string }
  | { action: "adRestore"; value: string }
  | { action: "balance" }
  // Команда из чата на настоящих данных: что именно сделать, понял сервис.
  | { action: "chat"; value: ChatCommand }
  // Ответ на предложение A/B-теста из «Сегодня» или отмена готовящегося теста.
  | { action: "test"; value: string; answer: "agree" | "decline" | "cancel" };
export type Action = Pending | { action: "clients"; value: number };

/** Сессия и оболочка: кто вошёл, какой сайт открыт, что показываем. */
export type SessionState = {
  /** Данные сервиса; null в прототипе на подставных данных. */
  live: Live | null;
  stage: "work" | "start";
  situation: Situation;
  mode: "panel" | "chat";
  view: View;
  /** Итог действия на панели; в чате итог виден в ленте. */
  notice: string;
  pending: Pending | null;
  /** Действие, ожидающее ответа сервиса; блокирует повторный запуск. */
  working: Pending | null;
  /** Вход в настоящем запуске состоялся, черновик перенесён в бизнес. */
  account: { email: string | null } | null;
  /** Список всех доступных бизнесов пользователя. */
  businesses: Business[];
  /** ID активного бизнеса. */
  activeBusinessId: string | null;
  /** Запуск для создания отдельного нового бизнеса (/?start=new). */
  createAsNew?: boolean;
  /** Режим распределения бюджета: раздельный по сайтам или единый общий пул. */
  budgetMode?: "separate" | "shared";
  /** Общая сумма бюджета на все сайты при shared-режиме. */
  sharedBudget?: number | null;
};

/** Чат: лента и поле ввода. */
export type ChatState = {
  input: string;
  msgs: Message[];
};

/** Мастер запуска: шаги, факты о бизнесе, подключения. */
export type WizardState = {
  step: number;
  site: string;
  codeCopied: boolean;
  inn: string;
  yandex: boolean;
  metrica: boolean;
  block: string;
  draftOff: Record<string, boolean>;
  /** Своя сумма на шаге бюджета; null, когда выбран готовый вариант. */
  budgetDraft: string | null;
  facts: Fact[];
  /** Открытая правка факта; id null у нового факта. */
  factEdit: { id: string | null; value: string; field?: Fact["field"] } | null;
  /** Разбор сайта до входа идёт: ключ доступа знает только этот браузер. */
  reading: { id: number; token: string } | "asking" | null;
  /** Пределы и варианты бюджета из правил сервиса; null — прототип на подставных. */
  budgetOptions: BudgetOptions | null;
  /** Данные для маркировки, кроме ИНН: ИНН живёт в поле inn. */
  advertiser: { kind: AdvertiserKind; name: string; phone: string; okved: string };
  /** Маркировка сохранена в сервисе, и владелец её не правит. */
  marked: boolean;
  /** Писатель пишет объявления мастера: ключ набора знает только этот браузер. */
  writing: { id: number; token: string } | "asking" | null;
  /** Готовый набор мастера; facts — по каким фактам писали, чтобы не писать заново без правок. */
  adSet: { id: number; token: string; template: boolean; needsReview: boolean; facts: string } | null;
};

/** Кабинет: объявления, история, период и настройки на экранах. */
export type CabinetState = {
  price: number | null;
  hist: HistoryEntry[];
  undone: Record<string, boolean>;
  undoing: Record<string, boolean>;
  asked: Record<string, boolean>;
  /** Последствия отмены от сервиса; в прототипе их нет, берём текст записи. */
  undoInfo: Record<string, UndoPreview>;
  adsOff: Record<string, boolean>;
  ads: Ad[];
  /** Открытое объявление на экране рекламы; "new" у создания нового. */
  openAd: string | null;
  paused: boolean;
  topped: boolean;
  decided: "added" | "kept" | null;
  extra: number;
  metric: "cost" | "leads";
  selDay: number | null;
  period: Period;
  filter: "all" | HistoryKind;
  clientsInput: string;
  clients: number | null;
  f: Record<SettingKey, string | number>;
};

export type State = SessionState & ChatState & WizardState & CabinetState;

export type BudgetOptions = { min: number; max: number; presets: number[] };
/** Шаги мастера после входа работают с бизнесом через эти методы. */
export type ReworkApi = Pick<Api, "rework" | "reworkSet" | "chooseRework">;
export type WizardApi = Pick<Api, "accounts" | "availableAccounts" | "attachAccount" | "connect" | "marking" | "saveMarking" | "tagCheck" | "requestTagCheck" | "launchCheck" | "startLaunch" | "wizardAds" | "manualTokenLink" | "saveManualToken">;
export type WizardDeps = {
  session: () => Promise<{ me: Me; businesses: Business[] } | null>;
  open: (businessId: string, csrf: string) => WizardApi;
};
export const LOGIN_URL = "/api/auth/yandex/login?then=start";
const LOGIN_FAILED: Record<string, string> = {
  denied: "Вход не состоялся: доступ в Яндексе не дан. Черновик сохранён, можно попробовать ещё раз.",
  unavailable: "Вход сейчас не работает. Черновик сохранён, попробуйте позже.",
};
const LOGIN_FAILED_OTHER = "Вход не состоялся. Черновик сохранён, попробуйте ещё раз.";

/** Разбор сайта до входа: POST /api/site-reads и GET /api/site-reads/{id}. */
export type GuestReader = {
  readSite: (url: string) => Promise<{ id: number; token: string; status: string; url: string }>;
  siteRead: (id: number, token: string, init?: RequestInit) => Promise<SiteRead>;
};

/** Писатель объявлений до входа: POST /api/creative-sets и GET /api/creative-sets/{id}. */
export type GuestWriter = {
  writeAds: (profile: AdDraftProfile) => Promise<{ id: number; token: string; status: string }>;
  adSet: (id: number, token: string, init?: RequestInit) => Promise<AdSet>;
};

/** Объявления писателя в карточки шага: номер в наборе живёт в id, выбор уходит по нему. */
export const writtenAds = (ads: WizardAd[]): Ad[] => ads.map((a, i) => ({
  id: `w${i}`,
  name: a.headline,
  tag: "",
  tagTone: "neutral",
  leads: 0,
  cost: 0,
  hook: a.headline,
  title: a.headline,
  text: a.text,
  facts: a.facts.length ? `Опирается на: ${a.facts.join(", ")}` : "Не опирается на факты профиля: общие слова об услуге",
}));

export const FACT_LABELS: Record<NonNullable<Fact["field"]>, string> = { niche: "Чем занимаетесь", address: "Где", service: "Услуга", feature: "Особенность" };
/** Факты разбора в карточки шага «Профиль»: цитата остаётся рядом, чтобы владелец видел, откуда это. */
export const siteFacts = (facts: SiteFact[]): Fact[] => facts.map((f, i) => ({
  id: `site${i}`,
  // Поле нужно переносу в профиль: без него услуги и адрес стали бы «особенностями».
  field: f.field,
  label: FACT_LABELS[f.field],
  value: f.price === null ? f.value : `${f.value}, ${rub(f.price)}`,
  source: "с сайта",
  quote: f.quote,
}));

let nextId = 1;
export const newFact = (value: string, field: Fact["field"] = "service"): Fact => ({
  id: "n" + nextId++,
  field,
  label: field ? FACT_LABELS[field] : "Вы добавили",
  value,
  source: "от вас",
});
export const msg = (who: Who, text: string, link?: Message["link"]): Message => ({ id: nextId++, who, text, link });

const GREETING = "Здравствуйте! Я веду рекламу малого бизнеса сам: создаю, слежу за деньгами, улучшаю. Начнём с вашего сайта. Напишите ссылку здесь или откройте «Настройку» и заполните всё там.";

export function initialState(stage: State["stage"] = "work", situation: Situation = "running"): State {
  return {
    live: null, stage, situation, mode: "panel", view: "overview", notice: "", pending: null, working: null, price: null, step: 0, input: "",
    msgs: [msg("ai", stage === "start" ? GREETING : fixtures.OPENING[situation])],
    hist: fixtures.histFor(situation), undone: {}, undoing: {}, asked: {}, undoInfo: {}, adsOff: {}, ads: fixtures.ADS, openAd: null, paused: false, topped: false, decided: null, extra: 0,
    metric: "cost", selDay: null, period: "week", filter: "all", clientsInput: "", clients: null,
    f: { services: "маникюр и педикюр", area: fixtures.TEXT.area, hours: "в часы работы", goal: 400, budget: 20000 },
    site: "", codeCopied: false, inn: "", yandex: false, metrica: false, block: "", draftOff: {}, budgetDraft: null, facts: fixtures.FACTS, factEdit: null, reading: null, budgetOptions: null, account: null, advertiser: { kind: "legal", name: "", phone: "", okved: "" }, marked: false, writing: null, adSet: null,
    businesses: [], activeBusinessId: null, budgetMode: "separate", sharedBudget: null,
  };
}

/** Расход и обращения за 7 дней и за 7 дней до них. */
export function week(s: State) {
  const d = s.live?.trail ?? fixtures.DAYS[s.situation];
  const sum = (a: typeof d, i: 0 | 1) => a.reduce((x, y) => x + y[i], 0);
  const last = d.slice(-7);
  const prev = d.slice(-14, -7);
  return { cost: sum(last, 0), leads: sum(last, 1), prevCost: sum(prev, 0), prevLeads: sum(prev, 1) };
}
export const daysOf = (s: State): Day[] => s.live?.days ?? fixtures.DAYS[s.situation];
export const statsOf = (s: State, period: Period): PeriodStats => s.live?.stats[period] ?? fixtures.STATS[period];
export const budgetOf = (s: State) => s.live?.budget ?? Number(s.f.budget) + s.extra;
export const spentOf = (s: State) => (s.live ? (s.live.spent ?? 0) : fixtures.DAYS[s.situation].reduce((x, d) => x + d[0], 0));
const STOPPING = new Set(["zero_balance", "balance_mismatch", "budget_stopped"]);
export const stopped = (s: State) => (s.live ? s.live.alerts?.some((a) => STOPPING.has(a.kind)) ?? false : s.situation === "problem" && !s.topped);
export const warned = (s: State) => (s.live ? (s.live.alerts?.length ?? 0) > 0 : s.situation === "budget" && !s.decided);
const liveStatus = (s: State) => (s.paused ? "На паузе" : stopped(s) ? "Показы остановлены" : warned(s) ? "Нужно внимание" : s.live?.hasCampaigns ? "Реклама идёт" : "Реклама не запущена");
export const statusText = (s: State) =>
  s.live ? liveStatus(s) : s.paused ? "На паузе" : stopped(s) ? "Показы остановлены" : warned(s) ? "Бюджет на исходе" : s.situation === "problem" ? "Жду деньги"
    : s.situation === "access" && !s.topped ? "Нет доступа" : s.situation === "first" ? "Собираю данные" : "Реклама идёт";
/** Название объявления с учётом новой цены из профиля. */
export const adName = (s: Pick<State, "price" | "ads">, id: string) => (s.price && id === "a2" ? `Маникюр от ${rub(s.price)}` : (s.ads.find((a) => a.id === id)?.name ?? id));
/** Что написано на объявлении: подставная цена не должна затирать уже переделанный текст. */
export function adShown(s: Pick<State, "price" | "ads">, ad: Ad) {
  const priced = !!s.price && ad.id === "a2" && ad.hook === fixtures.ADS[1]?.hook;
  return { hook: priced ? adName(s, ad.id) : ad.hook, title: priced ? adName(s, ad.id) : ad.title };
}
export function adStat(s: Pick<State, "adsOff" | "situation">, ad: Ad) {
  if (ad.tag === "в архиве" || ad.tag === "заменено") return "В архиве";
  if (s.adsOff[ad.id] || ad.tag === "отключено") return "Отключено";
  if (ad.tag === "готово к запуску") return "Готово к запуску";
  if (ad.tag === "отправляется") return "Отправляется в Яндекс";
  if (ad.tag === "на проверке Яндекса") return "На проверке Яндекса";
  // В первые дни показатели карточки ещё не накоплены: суммы из данных относятся к работающей рекламе.
  if (s.situation === "first" || ad.leads <= 0) return "Собираю данные";
  return `${ad.leads} ${leadsWord(ad.leads)}, по ${rub(ad.cost / ad.leads)}`;
}


/** Новый вариант по просьбе; пока писателя нет, прототип подставляет шаблоны из `src/mock`. */
export const draftAd = (s: State, base: Ad | null, ask: string, attempt = 0): { ad: Ad } | { refusal: string } => fixtures.draftAd(s, base, ask, attempt);

export const shown = (v: string | number) => (typeof v === "number" ? rub(v) : v);
/** Обе половины идущей проверки вариантов. */
export const inTest = (s: State, id: string) => s.situation === "running" && (id === "a1" || id === "a2");

/** Почему действие нельзя выполнить; общая проверка для панели и чата. */
export function refusalOf(s: State, a: Pending): string | null {
  if (a.action === "setting" && a.value[0] === "budget" && Number(a.value[1]) + s.extra < spentOf(s)) {
    const month = s.live ? "этом месяце" : "ноябре";
    return `В ${month} уже потрачено ${rub(spentOf(s))}, бюджет месяца не может быть меньше.`;
  }
  if ((a.action === "ad" || a.action === "adArchive") && !s.adsOff[a.value] && s.ads.filter((x) => x.tag !== "в архиве" && x.tag !== "заменено").every((x) => x.id === a.value || s.adsOff[x.id])) return "Это последнее объявление. Чтобы остановить показы, нажмите «Пауза».";
  return null;
}

/** Что изменится: показываем до применения. */
export function confirmText(s: State, a: Pending): string {
  switch (a.action) {
    case "chat": return a.value.confirm.replace(/^Понял так: /, "").replace(/ Применить\?$/, "");
    case "balance": return "Вы знаете причину расхождения? Сверка начнётся заново от нынешнего баланса. Показы сами не включатся.";
    case "test": return a.answer === "agree" ? "Проверить новый вариант рядом с этим объявлением? Бюджет не меняется." : a.answer === "decline" ? "Не проверять это объявление?" : "Отменить тест? Показы не менялись.";
    case "pause": return s.paused ? "Включить рекламу снова?" : "Остановить все показы? Настройки сохранятся.";
    case "setting": {
      const [key, val] = a.value;
      return key === "budget"
        ? `Бюджет ${rub(Number(val))} в месяц. Сначала обновлю лимит в Яндексе, потом перераспределю деньги. Отменить можно в истории.`
        : `${fixtures.OPTIONS[key].label}: ${shown(val)}. Общий бюджет не изменится. Отменить можно в истории.`;
    }
    case "undo": { const h = s.hist.find((x) => x.id === a.value); return `Отменить «${h?.title}»? ${h?.undoNote ?? ""}`; }
    case "price": return `Маникюр теперь от ${rub(a.value)}. Обновлю профиль и объявление со старой ценой.`;
    case "ad": if (s.live) return `Отключить «${adName(s, a.value)}»? Общий бюджет не изменится. Отменить можно в истории.`; return `Отключить «${adName(s, a.value)}»? ${inTest(s, a.value) ? "Идёт проверка вариантов, она закончится раньше срока. " : ""}Его деньги перераспределю между остальными. Отменить можно в истории.`;
    case "adArchive": return `Отправить «${adName(s, a.value)}» в архив? В любой момент можно вернуть обратно из вкладки «В архиве».`;
    case "adRestore": return `Вернуть «${adName(s, a.value)}» из архива?`;
    case "adPublish": return `Отправить «${adName(s, a.value)}» в Яндекс на проверку? ${a.grouping === "general" ? "Положу его в общую группу услуг." : "Подберу группу по услугам, на которые опирается текст."} Лимит кампании не изменится; показы начнутся после одобрения.`;
    case "adSave": {
      const isPrelaunch = s.live ? !s.live.hasCampaigns : s.situation === "first";
      if (!s.ads.some((x) => x.id === a.value.id)) {
        if (s.live) return `Сохранить черновик «${a.value.name}»? Объявление будет в разделе «Реклама» со статусом «К запуску». Оно ещё не отправлено в Яндекс.`;
        if (isPrelaunch) return `Добавить объявление «${a.value.name}»? Оно пойдёт в показы после запуска рекламы.`;
        return `Добавить объявление «${a.value.name}»? Яндекс проверит его, обычно до суток. Общий бюджет не вырастет: деньги поделю между объявлениями. Отключить можно в истории.`;
      }
      if (isPrelaunch) return `Сохранить изменения в «${adName(s, a.value.id)}»?`;
      return `Поставить новый вариант «${adName(s, a.value.id)}»? Старый работает, пока Яндекс проверяет новый, обычно до суток. ${inTest(s, a.value.id) ? "Идёт проверка вариантов, она закончится раньше срока. " : ""}Отменить можно в истории.`;
    }
  }
}

export class Store {
  state: State;
  private listeners = new Set<() => void>();
  constructor(state: State = initialState()) { this.state = state; }
  /** Есть — шаг «О бизнесе» читает настоящий сайт; нет — прототип на подставных фактах. */
  reader: GuestReader | null = null;
  /** Пауза между опросами разбора; воркер берёт разбор раз в минуту. */
  waitMs = 3000;
  /** Предельное время ожидания ответа сервиса при разборе сайта (мс). */
  readTimeoutMs = 120_000;
  /** Переход на другую страницу: вход Яндекса и кабинет. В тестах подменяется. */
  navigate = (url: string) => window.location.assign(url);
  private resumed = false;
  /** Перенос черновика в сервис; есть — вошедший владелец сохраняет мастер как новый сайт на шаге «Кабинет». */
  transferDeps: (TransferDeps & Partial<Pick<WizardDeps, "open">>) | null = null;
  /** У владельца уже был бизнес, и черновик в него намеренно не перенесён. */
  keptExisting = false;
  private saving = false;
  /** Бизнес мастера после входа; null — вход не состоялся или у владельца уже был бизнес. */
  wizard: WizardApi | null = null;
  /** Есть — «Переделать» на экране объявления просит настоящего писателя. */
  rework: ReworkApi | null = null;
  /** Есть — шаг «Объявления» пишет настоящий писатель; нет — подставные объявления прототипа. */
  writer: GuestWriter | null = null;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.state;
  /** Спросить про отмену записи истории или передумать. */
  askUndo = (id: string) => this.set({ asked: { ...this.state.asked, [id]: !this.state.asked[id] } });
  set = (patch: Partial<State>) => {
    this.state = { ...this.state, ...patch };
    if (this.state.stage === "start" && ("site" in patch || "facts" in patch || "step" in patch || "f" in patch || "draftOff" in patch || "adSet" in patch || "ads" in patch)) {
      this.persistDraft();
    }
    this.listeners.forEach((fn) => fn());
  };

  private persistDraft() {
    if (this.state.stage !== "start") return;
    const s = this.state;
    const kept = s.ads
      .map((a) => {
        if (s.draftOff[a.id] || a.id.startsWith("custom-")) return -1;
        const match = a.id.match(/^[wd](\d+)$/) || a.id.match(/^draft-(\d+)$/);
        return match ? Number(match[1]) : -1;
      })
      .filter((idx) => idx >= 0);
    const ads = s.adSet ? { ads: { id: s.adSet.id, token: s.adSet.token, kept } } : {};
    saveDraft({
      site: s.site,
      facts: s.facts,
      budget: Number(s.f.budget),
      step: s.step,
      draftOff: s.draftOff,
      customAds: s.ads.map((a) => ({ id: a.id, headline: a.title, text: a.text, facts: a.facts ? a.facts.split(", ") : [] })),
      ...(s.createAsNew || loadDraft()?.createAsNew ? { createAsNew: true } : {}),
      ...ads,
    });
  }

  // Прототипные действия живут в `MockStore` (src/mock); на настоящих данных их нет.
  run(_a: Action, _userText?: string): void { this.set({ pending: null, notice: "Это действие работает только на настоящих данных." }); }
  pickSituation = (_situation: Situation): void => undefined;
  reportTopUp = (): void => undefined;
  reconnect = (): void => undefined;
  addBudget = (): void => undefined;
  keepBudget = (): void => undefined;
  send(raw: string): void {
    const text = raw.trim();
    if (!text) return;
    if (this.state.stage === "start") return this.sendStart(text, text.toLowerCase());
    this.reply(text, "Это работает только на настоящих данных.");
  }

  /** Запросить подтверждение с панели: то же состояние, что у вопроса «Применить?» в чате. */
  ask(a: Pending) {
    const refusal = refusalOf(this.state, a);
    this.set(refusal ? { notice: refusal, pending: null } : { pending: a, notice: "" });
  }
  confirm = () => { if (this.state.pending) this.run(this.state.pending); };
  decline = () => this.set({ pending: null });
  goView = (view: View) => this.set({ view, mode: "panel", openAd: null });
  openAdScreen = (id: string): void => this.set({ view: "ads", mode: "panel", openAd: id, notice: "" });
  // Вопрос с панели не переносим в чат: там он не был задан словами.
  openChat = (input = "") => this.set({ mode: "chat", notice: "", input, pending: this.state.mode === "panel" ? null : this.state.pending });
  protected reply(userText: string, aiText: string, patch: Partial<State> = {}, link?: Message["link"]) {
    this.set({ ...patch, input: "", msgs: [...this.state.msgs, msg("me", userText), msg("ai", aiText, link)] });
  }
  protected sendStart(text: string, t: string) {
    const s = this.state;
    if (s.step === 0 && this.reader) { this.set({ site: text, input: "", msgs: [...s.msgs, msg("me", text)] }); void this.study(); return; }
    if (s.step === 0) { this.set({ site: s.site || text, input: "", step: 1, block: "", msgs: [...s.msgs, msg("me", text), msg("ai", STEPS[0]!.ai)] }); return; }
    if (s.step === 1 && !/верно|(^|\s)да(\s|$)|дальше/.test(t)) return this.reply(text, "Записал в профиль. Только такие факты попадут в рекламу.", { facts: [...s.facts, newFact(text)] });
    const num = Number((t.match(/\d[\d\s]*/) || ["0"])[0].replace(/\s/g, ""));
    const hasPause = /пауз|останов|выходн|воскрес/.test(t);
    const hasBudget = (s.step === 2 || /бюджет|трат/.test(t)) && num >= 1000;
    if (hasPause && hasBudget) {
      return this.reply(text, `Поставил ${rub(num)} в месяц. Расписание и паузы смогу настроить после запуска рекламы.`, { f: { ...s.f, budget: num } });
    }
    if (hasBudget) {
      return this.reply(text, `Поставил ${rub(num)} в месяц.`, { f: { ...s.f, budget: num } });
    }
    if (hasPause) {
      return this.reply(text, "Расписание и паузы смогу настроить после запуска рекламы. Сейчас настроим бюджет и объявления: напишите «дальше» или продолжите на вкладке «Настройка».");
    }
    if (/верно|(^|\s)да(\s|$)|дальше|нравится|запуск|продолж/.test(t)) { this.set({ input: "" }); return this.next(text); }
    this.reply(text, "Понял. Когда будете готовы, напишите «дальше» или нажмите кнопку на вкладке «Настройка».");
  }
  /** Сохранить правку факта: в рекламу попадают только подтверждённые человеком значения. */
  saveFact = () => {
    const { factEdit: edit, facts } = this.state;
    const value = edit?.value.trim();
    if (!edit || !value) return;
    const existing = edit.id !== null ? facts.find((f) => f.id === edit.id) : null;
    const field: Fact["field"] = edit.field ?? existing?.field ?? "service";
    const label = edit.field ? FACT_LABELS[edit.field] : existing?.label ?? FACT_LABELS[field];
    const next = edit.id === null
      ? [...facts, newFact(value, field)]
      : facts.map((f) => (f.id === edit.id ? { ...f, field, label, value, source: "от вас", quote: undefined } : f));
    this.set({ facts: next, factEdit: null, block: "", ...this.staleAds() });
  };
  removeFact = (id: string) => this.set({ facts: this.state.facts.filter((f) => f.id !== id), factEdit: null, block: "", ...this.staleAds() });
  /** Факты поменялись: написанные по старым фактам объявления больше не годятся, на шаге «Реклама» напишем новые. */
  private staleAds(): Partial<State> {
    return this.writer && this.state.adSet ? { ads: [], adSet: null, draftOff: {} } : {};
  }
  /** Разобрать сайт до входа. Шаг меняется, когда разбор готов; сбой оставляет путь вручную. */
  async study() {
    const reader = this.reader;
    if (!reader || this.state.reading) return;
    this.set({ reading: "asking", block: "" });
    try {
      const queued = await reader.readSite(this.state.site.trim());
      saveStartSite(queued.url);
      saveStartReading({ id: queued.id, token: queued.token, url: queued.url });
      this.set({ reading: { id: queued.id, token: queued.token }, site: queued.url });
      await this.awaitRead(reader, queued.id, queued.token);
    } catch (e) {
      clearStartReading();
      this.set({ reading: null, block: e instanceof ApiError ? e.message : "Сервис не ответил. Попробуйте ещё раз или заполните профиль вручную." });
    }
  }
  /** Продолжить опрос разбора сайта, запущенного до перезагрузки страницы. */
  async resumeReading(id: number, token: string, url: string) {
    const reader = this.reader;
    if (!reader || this.state.reading || this.state.step !== 0) return;
    this.set({ site: url, reading: { id, token }, block: "" });
    try {
      await this.awaitRead(reader, id, token);
    } catch (e) {
      clearStartReading();
      this.set({ reading: null, block: e instanceof ApiError ? e.message : "Сервис не ответил. Попробуйте ещё раз или заполните профиль вручную." });
    }
  }
  private async awaitRead(reader: GuestReader, id: number, token: string) {
    // Владелец ушёл с шага или начал чтение заново: этот опрос больше не нужен.
    const current = () => {
      const cur = this.state.reading;
      return !!cur && cur !== "asking" && cur.id === id;
    };
    const polled = await pollUntil({
      task: (signal) => reader.siteRead(id, token, { signal }),
      isDone: (read) => !current() || read.status === "failed" || read.status === "ready",
      intervalMs: this.waitMs,
      timeoutMs: this.readTimeoutMs,
    });
    if (polled.status === "aborted") return;
    if (polled.status === "timeout") {
      clearStartReading();
      return this.set({
        reading: null,
        block: "Разбор сайта занимает дольше обычного. Попробуйте ещё раз или заполните профиль вручную.",
      });
    }
    if (!current()) return;
    const read = polled.value;
    if (read.status === "failed") {
      clearStartReading();
      return this.set({ reading: null, block: read.problem_text ?? "Сайт не прочитался. Заполните профиль вручную." });
    }
    clearStartReading();
    const facts = siteFacts(read.facts);
    const ai = facts.length ? STEPS[0]!.ai : "На сайте не нашёл фактов, которые можно подтвердить цитатой. Добавьте их сами: в рекламу попадёт только это.";
    this.set({ reading: null, facts, step: 1, block: "", msgs: [...this.state.msgs, msg("ai", ai)] });
  }
  /** Сайта нет или он не читается: профиль с чистого листа. */
  fillByHand = () => {
    clearStartReading();
    this.set({ facts: [], step: 1, block: "", reading: null });
  };
  /** Прямой вход в аккаунт: если бизнес уже есть, сервер сразу откроет кабинет без мастера. */
  loginDirect = () => {
    this.navigate("/api/auth/yandex/login");
  };
  /** Уйти на вход Яндекса. Черновик ждёт в браузере: вход перезагружает страницу. */
  login = () => {
    this.persistDraft();
    this.navigate(LOGIN_URL);
  };
  /** Вернулись со входа: черновик становится бизнесом, профилем и планом бюджета. */
  async resume(deps: TransferDeps & Partial<Pick<WizardDeps, "open">>, loginResult: string | null) {
    // Эффект в режиме разработки запускается дважды: второй перенос создал бы второй бизнес.
    if (this.resumed) return;
    this.resumed = true;
    const draft = loadDraft();
    if (!draft) return;
    this.set({ facts: draft.facts, site: draft.site, f: { ...this.state.f, budget: draft.budget }, step: 4, block: "" });
    if (loginResult) return this.set({ block: LOGIN_FAILED[loginResult] ?? LOGIN_FAILED_OTHER });
    try {
      const done = await transferDraft(deps, draft);
      if (done.status === "signed_out") return;
      if (done.status === "exists") {
        this.keptExisting = true;
        return this.set({ block: `У вас уже есть бизнес «${done.name}». Черновик в него не переношу, чтобы не затереть профиль. Нажмите «Создать как новый проект» или «Продолжить», чтобы открыть кабинет.`, account: { email: null } });
      }
      this.wizard = deps.open?.(done.businessId, done.csrf) ?? null;
      this.set({ account: { email: done.email }, msgs: [...this.state.msgs, msg("ai", "Вы вошли. Профиль и бюджет сохранены в сервисе: дальше они живут в настройках кабинета.")] });
      await this.loadChosen();
    } catch (e) {
      this.set({ block: `${e instanceof Error ? e.message : "Сервис не ответил"}. Черновик сохранён: обновите страницу, чтобы повторить.` });
    }
  }
  /** Создать черновик как отдельный новый бизнес. */
  createAsNew = async (deps: TransferDeps & Partial<Pick<WizardDeps, "open">>) => {
    this.resumed = false;
    this.keptExisting = false;
    const draft = loadDraft();
    if (draft) saveDraft({ ...draft, createAsNew: true });
    await this.resume(deps, null);
  };
  /** Владелец уже вошёл: черновик мастера становится новым сайтом с профилем и планом бюджета. */
  private async saveNewSite(deps: TransferDeps & Partial<Pick<WizardDeps, "open">>) {
    if (this.saving) return;
    this.saving = true;
    const s = this.state;
    const kept = s.ads
      .map((a) => {
        if (s.draftOff[a.id] || a.id.startsWith("custom-")) return -1;
        const match = a.id.match(/^[wd](\d+)$/) || a.id.match(/^draft-(\d+)$/);
        return match ? Number(match[1]) : -1;
      })
      .filter((idx) => idx >= 0);
    const ads = s.adSet ? { ads: { id: s.adSet.id, token: s.adSet.token, kept } } : {};
    // Повтор после сбоя идёт в уже созданный бизнес, а не создаёт второй.
    const saved = loadDraft();
    const draft = saved?.businessId ? saved : {
      site: s.site, facts: s.facts, budget: Number(s.f.budget), createAsNew: true,
      draftOff: s.draftOff,
      customAds: s.ads.map((a) => ({ id: a.id, headline: a.title, text: a.text, facts: a.facts ? a.facts.split(", ") : [] })),
      ...ads,
    };
    saveDraft(draft);
    this.set({ block: "" });
    try {
      const done = await transferDraft(deps, draft);
      if (done.status === "signed_out") return this.set({ account: null, block: "Вход истёк. Войдите через Яндекс ещё раз: черновик сохранён." });
      if (done.status === "exists") return this.set({ block: "Не получилось сохранить новый сайт. Обновите страницу и попробуйте ещё раз." });
      this.wizard = deps.open?.(done.businessId, done.csrf) ?? null;
      this.set({ account: { email: done.email }, msgs: [...this.state.msgs, msg("ai", "Сохранил профиль и бюджет нового сайта. Теперь подключите к нему кабинет Директа.")] });
      await this.loadChosen();
    } catch (e) {
      this.set({ block: `${e instanceof Error ? e.message : "Сервис не ответил"}. Черновик сохранён: нажмите «Продолжить» ещё раз.` });
    } finally {
      this.saving = false;
    }
  }
  /** Вернулись с подключения кабинета: мастер продолжается с шага «Кабинет». */
  async resumeConnect(deps: WizardDeps, problem: string | null) {
    if (this.resumed) return;
    this.resumed = true;
    const id = loadWizard();
    const session = await deps.session().catch(() => null);
    const business = session?.businesses.find((b) => b.id === id);
    if (!session || !business) return this.navigate("/");
    this.wizard = deps.open(business.id, session.me.csrf_token);
    this.set({ account: { email: session.me.email }, step: 4, block: problem ?? "" });
    await this.loadChosen();
  }
  /** Выбор объявлений уже в сервисе: после перезагрузки сводка показывает его, а не подставные. */
  private async loadChosen() {
    if (!this.wizard || !this.writer) return;
    try {
      const { ads } = await this.wizard.wizardAds();
      this.set({ ads: writtenAds(ads), draftOff: {} });
    } catch {
      // Сводка готовности всё равно спросит сервис; здесь только подпись в боковой колонке.
    }
  }
  /** Набор пишут по фактам черновика; без правок фактов готовый набор не переписываем. */
  async writeAds(force = false) {
    const writer = this.writer;
    if (!writer || this.state.writing) return;
    const { niche, address, services, features } = draftProfile({ site: this.state.site, facts: this.state.facts, budget: 0 });
    const draft: AdDraftProfile = { niche, address, services, features, ...(force ? { force: true } : {}) };
    const facts = JSON.stringify(draft);
    if (!force && this.state.adSet?.facts === facts) return;
    this.set({ writing: "asking", adSet: null, ads: [], draftOff: {}, block: "" });
    try {
      const queued = await writer.writeAds(draft);
      this.set({ writing: { id: queued.id, token: queued.token } });
      const polled = await pollUntil({
        task: (signal) => writer.adSet(queued.id, queued.token, { signal }),
        isDone: (set) => set.status === "failed" || set.status === "ready",
        intervalMs: this.waitMs,
        timeoutMs: this.readTimeoutMs,
      });
      if (polled.status === "aborted") return;
      if (polled.status === "timeout") {
        return this.set({ writing: null, block: "Генерация объявлений занимает дольше обычного. Нажмите «Написать ещё раз»." });
      }
      const set = polled.value;
      if (set.status === "failed") return this.set({ writing: null, block: set.problem_text ?? "Не получилось написать объявления. Попробуйте ещё раз." });
      this.set({ writing: null, ads: writtenAds(set.ads), adSet: { id: queued.id, token: queued.token, template: set.template, needsReview: set.needs_review, facts } });
    } catch (e) {
      this.set({ writing: null, block: e instanceof ApiError ? e.message : "Сервис не ответил. Попробуйте ещё раз." });
    }
  }
  advance(ai?: string) {
    const s = this.state;
    this.set({ step: s.step + 1, msgs: [...s.msgs, msg("ai", ai ?? STEPS[s.step]!.ai)], block: "" });
  }
  private async saveMarking() {
    const s = this.state;
    if (!this.wizard) return;
    if (s.marked) return this.advance();
    const { kind, name, phone, okved } = s.advertiser;
    const inn = s.inn.trim();
    if (!/^(\d{10}|\d{12})$/.test(inn)) {
      return this.set({ block: "ИНН это 10 или 12 цифр." });
    }
    if (!name.trim()) {
      return this.set({ block: kind === "legal" ? "Укажите наименование организации." : "Укажите ФИО рекламодателя." });
    }
    if (kind === "person" && !phone?.trim()) {
      return this.set({ block: "Укажите контактный телефон." });
    }
    try {
      await this.wizard.saveMarking({ kind, inn, name: name.trim(), ...(kind === "person" ? { phone: phone.trim() } : { okved }) });
      // ИНН и реквизиты сохранены зашифрованными в сервисе; полный ИНН в браузере не остаётся
      this.set({ marked: true, inn: "" });
      this.advance();
    } catch (e) {
      this.set({ block: e instanceof Error ? e.message : "Сервис не ответил. Попробуйте ещё раз." });
    }
  }
  /** Шаги после входа в настоящем запуске: данные уходят в сервис, сводку проверяет сервис. */
  private nextReal(s: State) {
    if (s.step === 4 && !s.account) return this.set({ block: "Войдите через Яндекс: так профиль и бюджет сохранятся." });
    // Вошли раньше, мастер для нового сайта: сохраняем черновик отдельным бизнесом, а не выбрасываем.
    if (s.step === 4 && !this.wizard && this.transferDeps && !this.keptExisting) { void this.saveNewSite(this.transferDeps); return; }
    // Бизнес уже был: мастер его не трогает, дальше кабинет.
    if (s.step >= 4 && !this.wizard) { clearDraft(); return this.navigate("/"); }
    if (s.step === 4 && !s.yandex) return this.set({ block: "Подключите кабинет Директа: получите код доступа и вставьте токен." });
    if (s.step === 5) {
      if (s.marked) return this.advance();
      if (s.inn) { void this.saveMarking(); return; }
      return this.set({ block: "Укажите реквизиты рекламодателя в кабинете Директа и нажмите «Проверить снова»." });
    }
    if (s.step === 6) return this.advance("Проверьте сводку: в ней видно, что готово и чего не хватает.");
    if (s.step === STEPS.length - 1) { clearDraft(); clearWizard(); return this.navigate("/"); }
    return this.advance();
  }
  /** Следующий шаг запуска; держит на шаге, пока не выполнены условия. */
  next(userText?: string) {
    const s = this.state;
    if (s.factEdit) return this.set({ block: "Сохраните или отмените правку факта." });
    const limits = s.budgetOptions ?? { min: MIN_BUDGET, max: Infinity };
    if (s.step === 2 && s.budgetDraft !== null && Number(s.budgetDraft) < limits.min) return this.set({ block: `Укажите сумму от ${rub(limits.min)} в месяц: от 50 ₽ в день по правилам Яндекса плюс 10% резерва.` });
    if (s.step === 2 && s.budgetDraft !== null && Number(s.budgetDraft) > limits.max) return this.set({ block: `Больше ${rub(limits.max)} в месяц здесь не ставлю: проверьте, нет ли лишнего нуля.` });
    if (s.step === 2 && s.budgetDraft === null && (Number(s.f.budget) < limits.min || Number(s.f.budget) > limits.max)) {
      return this.set({ block: `Укажите сумму от ${rub(limits.min)} в месяц: от 50 ₽ в день по правилам Яндекса плюс 10% резерва.` });
    }
    if (s.step === 0 && !s.site.trim()) return this.set({ block: this.reader ? "Вставьте ссылку или заполните профиль вручную." : "Вставьте ссылку или расскажите о бизнесе в чате." });
    if (s.step === 0 && this.reader) { void this.study(); return; }
    if (s.step === 1 && s.facts.length === 0) return this.set({ block: "Добавьте хотя бы один факт: рекламу пишу только по ним." });
    if (s.step === 1 && !s.facts.some((f) => f.field === "service" || f.id === "prices")) {
      return this.set({ block: "Добавьте хотя бы одну услугу: объявления пишу по услугам." });
    }
    if (s.step === 2 && this.writer) { this.advance("Пишу объявления по вашим фактам. Посмотрите их на вкладке «Настройка» и уберите лишние."); void this.writeAds(); return; }
    if (s.step === 3 && this.writer && s.writing) return this.set({ block: "Объявления ещё пишутся: это обычно меньше минуты." });
    if (s.step === 3 && this.writer && !s.ads.length) {
      if (/писали|перегружен|лимит/i.test(s.block)) {
        return this.advance("Переходим к кабинету: объявления можно составить внутри настроек.");
      }
      return this.set({ block: "Объявлений пока нет: нажмите «Написать ещё раз»." });
    }
    // Без объявлений (исчерпан лимит) идём дальше: шаг «Реклама» сам обещает, что их напишем в кабинете.
    if (s.step >= 3 && s.ads.length > 0 && s.ads.every((a) => s.draftOff[a.id])) return this.set({ block: "Оставьте хотя бы одно объявление." });
    if (s.step >= 4 && this.reader) return this.nextReal(s);
    if (s.step === 4 && !s.yandex) return this.set({ block: "Подключите кабинет Яндекса." });
    if (s.step === 5 && !/^(\d{10}|\d{12})$/.test(s.inn)) return this.set({ block: "ИНН это 10 или 12 цифр." });
    if (s.step === 6 && !s.metrica) return this.set({ block: "Проверьте установку кода." });
    const msgs = [...s.msgs];
    if (userText) msgs.push(msg("me", userText));
    if (s.step === STEPS.length - 1) {
      msgs.push(msg("ai", "Запустил. Яндекс проверяет объявления, лимит поставлен в самом кабинете.\n\n" + fixtures.TEXT.rewind), msg("ai", fixtures.OPENING.running));
      return this.set({ stage: "work", mode: "panel", view: "overview", msgs, block: "" });
    }
    msgs.push(msg("ai", STEPS[s.step]!.ai));
    this.set({ step: s.step + 1, msgs, block: "" });
  }
}

export function useStoreState(store: Store): State {
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}

/** Поверхностное сравнение: те же ключи и те же значения по ссылке. */
export function shallowEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  return ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && Object.is((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

/**
 * Подписка на часть состояния: экран перерисовывается, только когда выбранное
 * изменилось (поверхностно). Селектор должен быть чистым и быстрым.
 */
export function useStore<T>(store: Store, select: (s: State) => T, isEqual: (a: T, b: T) => boolean = shallowEqual): T {
  const cache = useRef<{ state: State; select: (s: State) => T; value: T } | null>(null);
  const getSelection = () => {
    const state = store.getSnapshot();
    const prev = cache.current;
    if (prev && prev.state === state && prev.select === select) return prev.value;
    const value = select(state);
    const kept = prev && isEqual(prev.value, value) ? prev.value : value;
    cache.current = { state, select, value: kept };
    return kept;
  };
  return useSyncExternalStore(store.subscribe, getSelection, getSelection);
}
