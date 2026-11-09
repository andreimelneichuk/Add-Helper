// Новый интерфейс на настоящих данных: сводит ответы API к состоянию экранов и шлёт действия владельца.
// Деньги приходят целыми микроединицами; в рубли переводим только здесь и только для показа.
import { ApiError, createApi, createBusiness, createGuestApi, openSession } from "../api";
import type { AdLine, Alert, Api, ChatCommand, HistoryLine, Money, OutdatedAd, Overview, Profile, UndoPreview } from "../api";
import { clearDraft, clearStartReading, clearStartSite } from "./draft";
import { ALERT } from "./words";
import { MICROS_PER_RUBLE } from "./money";
import { leadsWord, rub } from "./data";
import type { Ad, Day, HistoryEntry, HistoryKind, Live, LiveAlert, Period, PeriodStats } from "./data";
import { budgetOf, initialState, msg, Store, week } from "./store";
import type { Message, State } from "./store";

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MONTHS_OF = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const KIND_TITLE: Record<string, string> = { set_limit: "Изменил дневной лимит", pause: "Остановил показы", resume: "Снял паузу", pause_ad: "Отключил объявление", resume_ad: "Включил объявление", set_schedule: "Изменил время показов", set_regions: "Изменил регион показов", create_campaign: "Создал кампанию", create_ad: "Отправил объявление на проверку", set_advertiser: "Передал данные для маркировки" };
const UNDOABLE = new Set(["set_limit", "resume", "pause_ad", "resume_ad", "set_schedule", "set_regions"]);
const STATUS_WORD: Record<string, string> = { planned: "Запланировано", applying: "Применяется", failed: "Не удалось", rejected: "Отклонено правилами", conflict: "Изменено кем-то ещё" };
const DAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];
const NO_ANSWER = "Сервис не ответил. Ничего не изменилось, попробуйте позже.";
const quote = (ad: OutdatedAd) => `«${ad.headline}» (${ad.numbers.join(", ")})`;

/** Что стало с объявлениями после правки фактов: цифры подставляет код, а не модель. */
export function savedText(outdated: OutdatedAd[]): string {
  if (outdated.length === 0) return "Сохранил. Новые объявления пишутся по этим фактам, в уже идущих объявлениях старых цифр нет.";
  const now = outdated.filter((a) => a.replacing);
  const later = outdated.filter((a) => !a.replacing);
  const parts = [`Сохранил. В уже идущих объявлениях остались старые цифры: ${outdated.map(quote).join(", ")}.`];
  if (now.length) parts.push(`Пишу замену по новым фактам (${now.length}), она встанет в очередь на запуск или попросит вашего решения в разделе «Реклама».`);
  if (later.length) parts.push(`Для ${later.length} лимит текстов на сегодня исчерпан, замену напишу завтра.`);
  parts.push("Пока Яндекс не проверил замену, старое объявление показывается. Если цена уже не действует, отключите его на экране объявления.");
  return parts.join(" ");
}

export const rubles = (micros: number | null | undefined) => (micros ?? 0) / MICROS_PER_RUBLE;
const rublesOrNull = (micros: number | null | undefined) => (micros === null || micros === undefined ? null : rubles(micros));
export const isWizardAdId = (id: string): boolean => !id.includes("/") && /^w\d+$/.test(id);
export const dayNumber = (day: string) => Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10))) / 86400000;
export const dayAt = (n: number) => new Date(n * 86400000).toISOString().slice(0, 10);
const sumMoney = (money: Money[], pick: (m: Money) => number | null) => money.reduce((x, m) => x + rubles(pick(m)), 0);

/** Дни подряд от и до: день без статистики это ноль, а не пропуск на графике. */
function fillDays(overview: Overview, since: string, until: string): Day[] {
  const known = new Map(overview.days.map((d) => [d.day, d.totals]));
  const days: Day[] = [];
  for (let n = dayNumber(since); n <= dayNumber(until); n++) {
    const totals = known.get(dayAt(n));
    days.push([rubles(totals?.cost), totals?.conversions ?? 0]);
  }
  return days;
}

export function periodStats(label: string, overview: Overview, since: string, until: string, ads: AdLine[]): PeriodStats {
  const inside = overview.days.filter((d) => d.day >= since && d.day <= until);
  const total = (pick: (t: Overview["totals"]) => number) => inside.reduce((x, d) => x + pick(d.totals), 0);
  const names = new Map(overview.campaigns.map((c) => [c.campaign_id, c.name]));
  const groups = new Map<string, AdLine[]>();
  for (const ad of ads) groups.set(ad.campaign_id, [...(groups.get(ad.campaign_id) ?? []), ad]);
  return {
    label,
    cost: rubles(total((t) => t.cost)),
    leads: total((t) => t.conversions),
    shows: total((t) => t.impressions),
    clicks: total((t) => t.clicks),
    dirs: [...groups].map(([id, lines]) => [
      names.get(id) ?? `Направление ${id}`,
      lines.reduce((x, a) => x + a.totals.conversions, 0),
      rubles(lines.reduce((x, a) => x + a.totals.cost, 0)),
      lines.map((a): [string, number, number, string] => [a.title, a.totals.conversions, rubles(a.totals.cost), `${a.account}/${a.ad_id}`]),
      `${lines[0]!.account}/${id}`,
    ]),
  };
}

// Состояние объявления площадки словами владельца: проверка Яндекса тоже состояние.
const AD_TAG: Record<string, [string, Ad["tagTone"]]> = {
  suspended: ["отключено", "neutral"],
  moderation: ["на проверке Яндекса", "accent"],
  rejected: ["отклонено Яндексом", "bad"],
  archived: ["заменено", "neutral"],
};

function toAd(line: AdLine, photoUrl: (id: number) => string): Ad {
  const [tag, tagTone] = AD_TAG[line.state] ?? ["работает", "neutral"];
  const ad: Ad = { id: `${line.account}/${line.ad_id}`, name: line.title, tag, tagTone, leads: line.totals.conversions, cost: rubles(line.totals.cost), hook: line.title, title: line.title, text: line.text, facts: "" };
  return line.image_id ? { ...ad, photo: photoUrl(line.image_id) } : ad;
}

function kindOf(line: HistoryLine): HistoryKind {
  if (line.source === "user" || line.source === "rollback") return "yours";
  if (line.source === "experiment") return "tests";
  return line.kind === "set_limit" || line.kind === "pause" ? "money" : "ads";
}

function toEntry(line: HistoryLine, ads: Ad[]): HistoryEntry {
  const about = ads.find((a) => line.target === `ad:${a.id.split("/").pop()}`);
  const canUndo = line.ref.startsWith("change-") && line.status === "applied" && line.source !== "external" && line.source !== "rollback" && UNDOABLE.has(line.kind) && !line.reverted;
  const when = new Date(line.at).toLocaleDateString("ru-RU", { day: "numeric", month: "short", timeZone: "Europe/Moscow" });
  return {
    id: line.ref,
    kind: kindOf(line),
    when,
    title: (line.source === "rollback" ? "Отмена. " : "") + (about ? `${KIND_TITLE[line.kind] ?? "Изменил"}: «${about.name}»` : (KIND_TITLE[line.kind] ?? "Оценил результат")),
    why: line.explanation,
    next: line.failure ? `Причина: ${line.failure}` : "",
    canUndo,
    undoNote: canUndo ? "Вернётся прежнее значение. Потраченные деньги не вернутся, общий бюджет не вырастет." : undefined,
    state: canUndo ? undefined : line.reverted ? "Отменено" : (STATUS_WORD[line.status] ?? "Записано"),
    about: about?.id,
  };
}

/** Причину и заголовок подставляет код сервиса по правилам; здесь только склейка. */
function rejectedText(details: Record<string, unknown>): string {
  const headline = typeof details.headline === "string" ? `«${details.headline}»` : "Объявление";
  const reason = typeof details.reason === "string" ? ` ${details.reason}.` : "";
  return `${headline} не прошло проверку Яндекса, переписать сам я не смог.${reason} Остальные работают. Попросите переделать его своими словами или отключите.`;
}

function alertOf(alert: Alert, money: Money | undefined): LiveAlert {
  const known = ALERT[alert.kind] ?? { title: "Нужна ваша проверка", tone: "warn" as const };
  const texts: Record<string, string> = {
    balance_mismatch: money ? `Со счёта ушло на ${rub(rubles(money.balance_gap))} больше, чем реклама потратила по статистике Яндекса. Показы остановлены, пока не ясно, куда уходят деньги.` : "Показы остановлены, пока не ясно, куда уходят деньги.",
    zero_balance: "Пополните счёт в кабинете Яндекса. Показы стоят, пока денег нет.",
    budget_stopped: "Перерасхода не будет. Показы включатся в новом месяце.",
    budget_stop_pending: "Яндекс пока не принял остановку, продолжаю пробовать. Лимиты стоят в самом кабинете.",
    budget_near_limit: "Когда бюджет закончится, показы остановятся сами. Перерасхода не будет.",
    platform_unavailable: "Пока Яндекс не отвечает, я ничего не меняю. Лимиты стоят в самом кабинете.",
    platform_not_connected: "Пока кабинет не подключён, изменения не дойдут до Яндекса. Подключить его можно в настройках.",
    ads_no_profile: "Добавьте услуги в профиль в настройках: в рекламу попадают только факты из него.",
    ads_need_confirmation: "Посмотрите их в разделе «Реклама» и решите, запускать ли.",
    ads_rejected: rejectedText(alert.details),
  };
  return { kind: alert.kind, title: known.title, text: texts[alert.kind] ?? "Подробности в истории.", tone: known.tone === "error" ? "bad" : "warn" };
}

function profileLines(profile: Profile | null): [string, string][] {
  if (!profile) return [];
  const services = profile.services.map((x) => (x.price === null ? x.name : `${x.name}, ${rub(x.price)}`)).join("; ");
  const lines: [string, string][] = [["Чем занимаетесь", profile.niche], ["Услуги и цены", services], ["Адрес", profile.address], ["Особенности", profile.features.join(", ")], ["Сайт", profile.website]];
  return lines.filter(([, value]) => value);
}

export function platformToday(): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());
  } catch {
    const msk = new Date(Date.now() + 3 * 3600 * 1000);
    return msk.toISOString().slice(0, 10);
  }
}

export async function loadLive(api: Api, name: string): Promise<Partial<State>> {
  const currentToday = platformToday();
  const estMonthStart = `${currentToday.slice(0, 8)}01`;
  const estWeekStart = dayAt(dayNumber(currentToday) - 6);
  const estTrailStart = dayAt(dayNumber(currentToday) - 13);
  const estSince = estMonthStart < estTrailStart ? estMonthStart : estTrailStart;
  const [overview, adsWeek, adsMonth, history, profile, today_, plan] = await Promise.all([
    api.overview({ since: estSince }),
    api.ads({ since: estWeekStart }),
    api.ads({ since: estMonthStart }),
    api.history(),
    api.profile().catch(() => null),
    // Без ленты сервиса экран соберёт вывод сам из тех же данных.
    api.today().catch(() => null),
    api.budgetPlan ? api.budgetPlan().catch(() => null) : Promise.resolve(null),
  ]);
  const today = overview.today || currentToday;
  const monthStart = `${today.slice(0, 8)}01`;
  const weekStart = dayAt(dayNumber(today) - 6);
  const trailStart = dayAt(dayNumber(today) - 13);
  const since = monthStart < trailStart ? monthStart : trailStart;
  const month = Number(today.slice(5, 7)) - 1;
  const year = Number(today.slice(0, 4));
  const all = fillDays(overview, since, today);
  const money = overview.money[0];
  const seen = new Set(adsWeek.map((a) => a.ad_id));
  const lines = [...adsWeek, ...adsMonth.filter((a) => !seen.has(a.ad_id)).map((a) => ({ ...a, totals: { impressions: 0, clicks: 0, cost: 0, conversions: 0 } }))];
  let ads = lines.map((line) => toAd(line, api.photoUrl));
  if (api.wizardAds) {
    try {
      const wizard = await api.wizardAds(true);
      if (wizard?.ads?.length) {
        ads = [...ads, ...wizard.ads.flatMap((w, i) => {
          if (w.submitted) return [];
          const isArchived = !!w.archived;
          const isActive = w.active !== false && !isArchived;
          const tag = isArchived ? "в архиве" : isActive ? "готово к запуску" : "отключено";
          const tagTone: Ad["tagTone"] = isArchived ? "neutral" : isActive ? "accent" : "neutral";
          return {
            id: w.id || `w${i}`,
            name: w.headline,
            tag,
            tagTone,
            leads: 0,
            cost: 0,
            hook: w.headline,
            title: w.headline,
            text: w.text,
            facts: Array.isArray(w.facts) ? w.facts.join(", ") : "",
          };
        })];
      }
    } catch { /* без мастера */ }
  }
  const running = overview.campaigns.filter((c) => c.state !== "archived");
  const alerts = overview.alerts.map((a) => alertOf(a, money));
  const stats: Record<Period, PeriodStats> = {
    week: periodStats("7 дней", overview, weekStart, today, adsWeek),
    month: periodStats(`С 1 ${MONTHS_OF[month]}`, overview, monthStart, today, adsMonth),
  };
  const live: Live = {
    cards: today_?.cards,
    name,
    today,
    monthName: MONTHS[month]!,
    monthOf: MONTHS_OF[month]!,
    monthDays: new Date(Date.UTC(year, month + 1, 0)).getUTCDate(),
    days: all.slice(dayNumber(monthStart) - dayNumber(since)),
    trail: all.slice(-14),
    spent: money && money.period.spent !== null ? sumMoney(overview.money, (m) => m.period.spent) : null,
    budget: sumMoney(overview.money, (m) => m.period.hard_limit),
    balance: money && money.period.last_balance !== null
      ? rublesOrNull(money.period.last_balance)
      : rublesOrNull(overview.account_balance_micros),
    gap: money ? rublesOrNull(money.balance_gap) : null,
    account: money?.period.account ?? overview.account_login ?? null,
    hasCampaigns: running.length > 0,
    planBudget: plan?.rubles ?? null,
    alerts,
    stats,
    profile: profileLines(profile),
    rawProfile: profile ?? null,
  };
  return {
    live,
    ads,
    hist: history.map((line) => toEntry(line, ads)),
    adsOff: Object.fromEntries(ads.filter((a) => a.tag === "отключено").map((a) => [a.id, true])),
    // Ответ о клиентах показываем, только если он за то же окно, что и цифры недели: иначе цена клиента соврёт.
    clients: overview.clients && overview.clients.since === weekStart && overview.clients.until === today ? overview.clients.clients : null,
    paused: running.length > 0 && running.every((c) => c.state === "suspended") && !alerts.some((a) => a.tone === "bad"),
  };
}

function summary(s: State): string {
  const w = week(s);
  if (w.leads === 0) return w.cost > 0 ? `За 7 дней потрачено ${rub(w.cost)}, обращений пока нет.` : "За 7 дней показов и трат не было.";
  return `За 7 дней ${w.leads} ${leadsWord(w.leads)} по ${rub(w.cost / w.leads)}, неделей раньше ${w.prevLeads}.`;
}

const FEED_KEY = "adhelper.chat.";
const FEED_MAX = 60;

/** Сохранённая лента сайта; ключ по бизнесу, чтобы разговоры сайтов не смешивались. */
function loadFeed(id: string | null): Message[] {
  if (!id) return [];
  try {
    const raw = window.sessionStorage.getItem(FEED_KEY + id);
    const list: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list
      .filter((m): m is Message => typeof m?.text === "string" && ["ai", "me", "note"].includes(m?.who))
      .map((m) => msg(m.who, m.text, m.link));
  } catch {
    return [];
  }
}

function saveFeed(id: string | null, msgs: Message[]) {
  if (!id) return;
  try {
    const kept = msgs.slice(-FEED_MAX).map(({ who, text, link }) => ({ who, text, link }));
    window.sessionStorage.setItem(FEED_KEY + id, JSON.stringify(kept));
  } catch { /* без хранилища лента живёт до перезагрузки */ }
}

const NOT_LAUNCHED = "Реклама ещё не запущена: в Директе нет направлений. Сначала подключите кабинет и завершите запуск в настройках.";

export class LiveStore extends Store {
  private queue: ChatCommand[] = [];
  private budgetMax: number | null = null;
  public override rework: Api | null = null;
  constructor(public api: Api, loaded: Partial<State>) {
    super({ ...initialState(), msgs: [], ...loaded });
    const saved = loadFeed(this.state.activeBusinessId);
    this.state = { ...this.state, msgs: saved.length ? saved : [msg("ai", summary(this.state))] };
    this.rework = api;
    // Лента живёт до закрытия вкладки: перезагрузка страницы не стирает разговор.
    let last = this.state.msgs;
    this.subscribe(() => {
      if (this.state.msgs === last) return;
      last = this.state.msgs;
      saveFeed(this.state.activeBusinessId, last);
    });
  }

  /** Перечитать данные после действия, которое прошло мимо журнала: профиль, решения по объявлениям. */
  async refresh() {
    this.set(await loadLive(this.api, this.state.live?.name ?? ""));
  }

  /**
   * Общий бюджет на все сайты. Сервер хранит режим и делит сумму; живые лимиты каждого запущенного сайта
   * меняются его же проверенным путём (журнал, снижение лимитов раньше смены бюджета). Сначала сайты,
   * где бюджет снижается, потом где растёт: сумма лимитов не превышает большую из старой и новой.
   * На первой ошибке останавливаемся и называем, что уже изменено.
   */
  async applySharedBudget(total: number): Promise<string> {
    const session = await openSession();
    if (!session) throw new Error("Сессия не найдена. Войдите заново.");
    const saved = await this.api.saveBudgetPortfolio({ mode: "shared", rubles: total });
    const sites = await Promise.all(session.businesses.map(async (b) => {
      const api = createApi(b.id, session.me.csrf_token);
      const share = saved.shares.find((x) => x.business_id === b.id)?.rubles ?? 0;
      const current = sumMoney((await api.overview()).money, (m) => m.period.hard_limit);
      return { id: b.id, name: b.name, api, share, current };
    }));
    // Сайты без запущенного периода получили долю в плане: лимиты им поставит запуск.
    const changes = sites.filter((x) => x.current > 0 && x.share !== x.current).sort((a, b) => (a.share - a.current) - (b.share - b.current));
    const done: string[] = [];
    for (const site of changes) {
      try {
        await site.api.setBudget(site.share, crypto.randomUUID());
        done.push(site.name);
      } catch (e) {
        const reason = e instanceof Error ? e.message : "Сервис не ответил";
        const before = done.length ? `Уже изменил: ${done.map((n) => `«${n}»`).join(", ")}. ` : "Лимиты не менялись. ";
        throw new Error(`Остановился на бизнесе «${site.name}»: ${reason}. ${before}Остальные бизнесы не менял, сумма лимитов не выросла.`);
      }
    }
    await this.refresh();
    const n = sites.length;
    return `Общий бюджет ${rub(total)} в месяц на ${n} ${n === 1 ? "бизнес" : n < 5 ? "бизнеса" : "бизнесов"} сохранён. Бюджет изменился у ${done.length} из ${changes.length || 0} запущенных. Режим хранится в сервисе.`;
  }

  /** Переключить активный бизнес на другой из списка. */
  async switchBusiness(id: string) {
    try { window.localStorage.setItem("adhelper.activeBusinessId", id); } catch { /* без хранилища */ }
    const name = this.state.businesses.find((b) => b.id === id)?.name;
    // Загрузка занимает секунду: без отклика кажется, что нажатие не сработало.
    if (name) this.set({ notice: `Переключаю на бизнес «${name}»…` });
    const session = await openSession();
    if (!session) return;
    const business = session.businesses.find((b) => b.id === id);
    if (!business) return;
    const newApi = createApi(business.id, session.me.csrf_token);
    this.api = newApi;
    this.rework = newApi;
    const loaded = await loadLive(newApi, business.name);
    const saved = loadFeed(id);
    this.set({
      ...loaded,
      msgs: saved.length ? saved : [msg("ai", summary({ ...this.state, ...loaded }))],
      pending: null,
      activeBusinessId: id,
      businesses: session.businesses,
      notice: `Вы переключились на бизнес «${business.name}»`,
    });
  }

  /** Удалить бизнес: отвязать кабинет, остановить/архивировать кампании и переключиться. */
  async deleteBusiness(id: string) {
    await this.api.deleteBusiness(id);
    const session = await openSession();
    const remaining = session?.businesses.filter((b) => b.id !== id) ?? this.state.businesses.filter((b) => b.id !== id);
    this.set({ businesses: remaining });
    if (remaining.length > 0) {
      const next = remaining[0]!;
      await this.switchBusiness(next.id);
      this.set({ notice: `Бизнес удалён. Переключил на «${next.name}».` });
    } else {
      try { window.localStorage.removeItem("adhelper.activeBusinessId"); } catch {}
      clearDraft();
      clearStartSite();
      clearStartReading();
      window.location.assign("/?start=new");
    }
  }

  /** Открыть мастер создания нового сайта или бизнеса для этого же аккаунта. */
  addNewSite() {
    clearDraft();
    clearStartSite();
    clearStartReading();
    window.location.assign("/?start=new");
  }

  /** Создать новый бизнес/сайт прямо из панели и переключиться на него. */
  async createSite(name: string, website?: string) {
    const session = await openSession();
    if (!session) throw new Error("Сессия не найдена. Войдите заново.");
    const trimmed = name.trim();
    if (!trimmed) throw new Error("Укажите название бизнеса или сайта.");
    const created = await createBusiness(trimmed, session.me.csrf_token);
    try { window.localStorage.setItem("adhelper.activeBusinessId", created.id); } catch {}
    const newApi = createApi(created.id, session.me.csrf_token);
    this.api = newApi;
    this.rework = newApi;
    if (website?.trim()) {
      try {
        const p = await newApi.profile().catch(() => null);
        if (p) await newApi.saveProfile({ ...p, website: website.trim() });
      } catch {}
    }
    const freshSession = await openSession();
    const loaded = await loadLive(newApi, created.name);
    this.set({
      ...loaded,
      msgs: [msg("ai", summary({ ...this.state, ...loaded }))],
      pending: null,
      activeBusinessId: created.id,
      businesses: freshSession?.businesses ?? [...this.state.businesses, created],
      notice: `Создан новый сайт «${created.name}». Заполните профиль в настройках.`,
    });
    return created;
  }

  /** Перед вопросом «отменить?» узнаём у сервиса, что именно вернётся и можно ли вообще. */
  override askUndo = (id: string) => {
    if (this.state.asked[id]) return this.set({ asked: { ...this.state.asked, [id]: false } });
    const show = (info: UndoPreview) => this.set({ undoInfo: { ...this.state.undoInfo, [id]: info }, asked: { ...this.state.asked, [id]: true } });
    this.api.rollbackPreview(Number(id.replace("change-", ""))).then(show, (error: unknown) =>
      show({ possible: false, status: "error", directions: 0, text: error instanceof ApiError ? error.message : "Сервис не ответил. Ничего не изменилось, попробуйте позже." }),
    );
  };

  /** Подробности открытого объявления; список их не отдаёт. */
  override openAdScreen = (id: string) => {
    this.set({ view: "ads", mode: "panel", openAd: id, notice: "" });
    const [account, adId] = id.split("/");
    if (!account || !adId) return;
    this.api.ad(account, adId).then((detail) => {
      const labels = [...new Set(detail.basis.sources.map((x) => x.label))].join(", ");
      const facts = labels ? labels[0]!.toUpperCase() + labels.slice(1) : "В тексте нет фактов из профиля";
      this.set({ ads: this.state.ads.map((a) => (a.id === id ? { ...a, facts } : a)) });
    }).catch(() => undefined);
  };

  /** Что сделать на сервисе. Текст итога пишем только после ответа: состояние приходит с сервера. */
  private async perform(a: NonNullable<State["pending"]>): Promise<string> {
    const s = this.state;
    switch (a.action) {
      case "pause": {
        if (s.paused) {
          const { refused } = await this.api.resume(crypto.randomUUID());
          const budget = budgetOf(s);
          const check = budget > 0 ? `бюджет ${rub(budget)}` : "бюджет месяца";
          const rest = refused.length ? " Часть направлений не включил: их остановили не вы, а защита бюджета." : "";
          return `Снял паузу, показы идут. Вы попросили. Перед включением проверил, что расход не выйдет за ${check}.${rest} Снова остановить можно кнопкой «Пауза».`;
        }
        const { stopped } = await this.api.pause(crypto.randomUUID());
        return stopped.length ? "Поставил на паузу. Настройки сохранены, запись в истории." : "Идущих показов нет, останавливать нечего.";
      }
      case "ad": {
        const active = !!s.adsOff[a.value];
        const name = s.ads.find((x) => x.id === a.value)?.name ?? "";
        if (isWizardAdId(a.value)) {
          if (this.api.setWizardAdState) {
            await this.api.setWizardAdState(a.value, active);
          }
          const nextOff = { ...this.state.adsOff, [a.value]: !active };
          const updatedAds = this.state.ads.map((x) =>
            x.id === a.value
              ? { ...x, tag: active ? "готово к запуску" : "отключено", tagTone: active ? ("accent" as const) : ("neutral" as const) }
              : x
          );
          this.set({ ads: updatedAds, adsOff: nextOff });
          return `${active ? "Включил" : "Отключил"} «${name}». ${active ? "Объявление пойдёт в показы после запуска." : "Оно исключено из запуска."}`;
        }
        const [account, adId] = a.value.split("/");
        if (!account || !adId) {
          return "Объявление ещё не создано в Директе.";
        }
        const { status } = await this.api.setAdState(account, adId, active, crypto.randomUUID());
        if (status === "pending") return `Запрос про «${name}» записан, Яндекс ещё применяет его.`;
        return `${active ? "Включил" : "Отключил"} «${name}». Общий бюджет не изменился. Запись в истории.`;
      }
      case "adPublish": {
        if (!this.api.publishWizardAd) throw new Error("Не удалось отправить объявление. Обновите страницу и попробуйте ещё раз.");
        const ad = s.ads.find((x) => x.id === a.value);
        if (!ad) throw new Error("Объявление уже изменилось. Обновите страницу.");
        const result = await this.api.publishWizardAd(a.value, a.grouping);
        await this.refresh();
        if (result.status === "refused") return result.text ?? "Яндекс не принял объявление. Подробности доступны в истории.";
        return result.status === "pending"
          ? `Запрос на публикацию «${ad.name}» записан. Яндекс ещё применяет его.`
          : `Отправил «${ad.name}» в группу «${result.group}» на проверку Яндексу. Показы начнутся после одобрения.`;
      }
      case "adSave": {
        const ad = a.value;
        const isNew = !s.ads.some((x) => x.id === ad.id);
        if (isNew) {
          if (this.api.createWizardAd) {
            const created = await this.api.createWizardAd({ headline: ad.title, text: ad.text });
            const newAd: Ad = {
              id: created.id || `w${s.ads.length}`,
              name: created.headline,
              tag: "готово к запуску",
              tagTone: "accent",
              leads: 0,
              cost: 0,
              hook: created.headline,
              title: created.headline,
              text: created.text,
              facts: Array.isArray(created.facts) ? created.facts.join(", ") : "Ваш текст",
            };
            this.set({ ads: [newAd, ...this.state.ads], openAd: null });
            return `Добавил объявление «${created.headline}». Оно готово к запуску.`;
          }
        } else {
          if (isWizardAdId(ad.id) && this.api.updateWizardAd) {
            await this.api.updateWizardAd(ad.id, { headline: ad.title, text: ad.text });
          }
          const updatedAds = this.state.ads.map((x) =>
            x.id === ad.id ? { ...x, title: ad.title, hook: ad.title, name: ad.title, text: ad.text } : x
          );
          this.set({ ads: updatedAds, openAd: null });
          return `Сохранил изменения в «${ad.title}».`;
        }
        return `Обновление сохранено.`;
      }
      case "adArchive": {
        const id = a.value;
        const name = s.ads.find((x) => x.id === id)?.name ?? "";
        if (isWizardAdId(id) || !s.live?.hasCampaigns) {
          if (this.api.archiveWizardAd) {
            await this.api.archiveWizardAd(id, true);
          }
        } else {
          const [account, adId] = id.split("/");
          if (account && adId && this.api.archiveAd) {
            const { status } = await this.api.archiveAd(account, adId);
            if (status === "pending") {
              return `Яндекс ещё останавливает показы «${name}». Дождитесь остановки и повторите архивацию.`;
            }
          }
        }
        const updatedAds = this.state.ads.map((x) =>
          x.id === id ? { ...x, tag: "в архиве", tagTone: "neutral" as const } : x
        );
        this.set({ ads: updatedAds, openAd: null });
        return `Отправил объявление «${name}» в архив.`;
      }
      case "adRestore": {
        const id = a.value;
        const name = s.ads.find((x) => x.id === id)?.name ?? "";
        if (isWizardAdId(id) || !s.live?.hasCampaigns) {
          if (this.api.archiveWizardAd) {
            await this.api.archiveWizardAd(id, false);
          }
        } else {
          const [account, adId] = id.split("/");
          if (account && adId) {
            await this.api.setAdState(account, adId, true, crypto.randomUUID());
          }
        }
        const isLive = !!s.live?.hasCampaigns && !isWizardAdId(id);
        const updatedAds = this.state.ads.map((x) =>
          x.id === id
            ? {
                ...x,
                tag: isLive ? "работает" : "готово к запуску",
                tagTone: isLive ? ("good" as const) : ("accent" as const),
              }
            : x
        );
        const nextOff = { ...this.state.adsOff };
        delete nextOff[id];
        this.set({ ads: updatedAds, adsOff: nextOff, openAd: null });
        return `Вернул объявление «${name}» из архива.`;
      }
      case "undo": {
        const h = s.hist.find((x) => x.id === a.value);
        const { status } = await this.api.rollback(Number(a.value.replace("change-", "")), !!a.whole);
        if (status === "pending") return "Яндекс не ответил, отмена записана и будет повторена.";
        this.set({ undone: { ...this.state.undone, [a.value]: true } });
        const where = a.whole ? ` для всех ${s.undoInfo[a.value]?.directions ?? ""} направлений` : "";
        return `Отменил${where}: ${h?.title ?? "изменение"}. Исходная запись осталась в истории.`;
      }
      case "setting": {
        const [key, value] = a.value;
        if (key !== "budget") return "Это действие ещё не подключено к сервису, ничего не изменилось.";
        const before = budgetOf(s);
        const { cuts } = await this.api.setBudget(Number(value), crypto.randomUUID());
        const how = cuts
          ? `Сначала снизил лимиты ${cuts} ${cuts === 1 ? "направления" : "направлений"} в Яндексе, потом сменил бюджет: так расход не выйдет за новую сумму ни в какой момент.`
          : "Лимиты в Яндексе не менялись: они уже помещаются в новую сумму.";
        return `Бюджет месяца теперь ${rub(Number(value))}, был ${rub(before)}. Вы попросили. ${how} Вернуть прежний можно здесь же, в настройках.`;
      }
      case "chat":
        return this.performChat(a.value);
      case "test": {
        const id = Number(a.value);
        const { text } = a.answer === "cancel" ? await this.api.cancelTest(id) : await this.api.answerTest(id, a.answer === "agree");
        return `${text}.`;
      }
      case "balance":
        await this.api.confirmBalance(s.live!.account!);
        return "Сверка начнётся заново от нынешнего баланса. Показы сами не включатся.";
      default:
        return "Это действие ещё не подключено к сервису, ничего не изменилось.";
    }
  }

  /** Команда из чата идёт теми же маршрутами, что и кнопки: политика, журнал, исполнитель. */
  private async performChat(c: ChatCommand): Promise<string> {
    switch (c.kind) {
      case "set_price": {
        const profile = await this.api.profile();
        if (!profile.services.some((x) => x.name === c.service)) return `Услуги «${c.service}» уже нет в профиле, ничего не изменил.`;
        const services = profile.services.map((x) => (x.name === c.service ? { ...x, price: c.rubles } : x));
        const { outdated } = await this.api.saveProfile({ ...profile, services });
        return `Цена «${c.service}» в профиле теперь ${rub(c.rubles ?? 0)}. Вы попросили. ${savedText(outdated ?? []).replace(/^Сохранил\. /, "")}`;
      }
      case "skip_days": {
        const names = c.days.map((d) => DAY_NAMES[d - 1]).join(", ");
        const { applied, unknown } = await this.api.skipDays(c.days.map((d) => d - 1), crypto.randomUUID());
        if (!applied.length) return `Реклама и так не показывается: ${names}. Ничего не изменил.`;
        const rest = unknown.length ? ` У ${unknown.length} ${unknown.length === 1 ? "направления" : "направлений"} часы ещё не прочитаны у Яндекса, их не трогал.` : "";
        return `Больше не показываю рекламу: ${names}. Часы каждого направления как были, бюджет месяца не изменился. Вы попросили.${rest} Вернуть можно отменой в истории.`;
      }
      default:
        return "Это действие ещё не подключено к сервису, ничего не изменилось.";
    }
  }

  /** Сервис переводит сообщение в команду из закрытого набора; меняющие ждут «да». */
  private async understand(text: string) {
    let c: ChatCommand;
    try {
      c = await this.api.command(text);
    } catch (error) {
      this.say(error instanceof ApiError ? error.message : NO_ANSWER);
      return;
    }
    // Предел суммы из правил плана: тот же, что предупреждает о лишнем нуле в мастере.
    if ([c, ...(c.next ?? [])].some((x) => x.kind === "set_budget") && this.budgetMax === null) {
      this.budgetMax = await createGuestApi().budgetOptions().then((o) => o.max, () => null);
    }
    // Несколько просьб в одном сообщении: по одной, у каждой своё «да».
    this.queue = [...(c.next ?? [])];
    if (this.queue.length) this.say(`Вижу ${this.queue.length + 1} просьбы, спрошу про каждую по очереди.`);
    this.handle(c);
    this.nextRequest();
  }

  /** Следующая просьба из того же сообщения, когда прошлая решена. */
  private nextRequest() {
    while (!this.state.pending && this.queue.length) this.handle(this.queue.shift()!);
  }

  private handle(c: ChatCommand) {
    const s = this.state;
    switch (c.kind) {
      case "pause":
      case "resume":
        // Без направлений в Директе нечего ни останавливать, ни включать: говорим сразу, без «Применить?».
        if (s.live && !s.live.hasCampaigns) {
          const isConnected = !!(s.live.account || s.yandex);
          const hasDraftAds = s.ads.some((a) => isWizardAdId(a.id) || a.tag === "готово к запуску");
          const text = isConnected
            ? (hasDraftAds
              ? "Реклама ещё не запущена: объявления готовы к запуску, но кампании в Директе ещё не созданы. Чтобы начать показы, завершите запуск рекламы."
              : "Реклама ещё не запущена: кабинет Директа подключён, но кампании ещё не созданы. Чтобы создать кампании и начать показы, завершите запуск рекламы.")
            : NOT_LAUNCHED;
          return this.say(text, { label: isConnected ? "Запустить рекламу" : "Открыть настройки", view: isConnected ? "overview" : "settings" });
        }
        if (c.kind === "resume") {
          if (!s.paused) return this.say("Реклама и так идёт, включать нечего.");
          return this.offer(c.confirm, { action: "pause" });
        }
        if (s.paused) return this.say("Реклама уже на паузе. Чтобы включить, напишите «включи».");
        return this.offer(c.confirm, { action: "pause" });
      case "set_budget": {
        const amount = c.rubles ?? 0;
        const spent = s.live?.spent ?? 0;
        if (amount < spent) return this.say(`В этом месяце уже потрачено ${rub(spent)}, бюджет не может быть меньше.`);
        const lower = amount < budgetOf(s);
        const huge = this.budgetMax !== null && amount > this.budgetMax
          ? ` Это больше ${rub(this.budgetMax)}: проверьте, нет ли лишнего нуля.`
          : "";
        return this.offer(`Понял так: бюджет ${rub(amount)} в месяц.${huge}${lower ? " Сначала снижу лимиты в Яндексе, потом сменю бюджет." : ""} Применить?`, { action: "setting", value: ["budget", amount] });
      }
      case "ad_off":
      case "ad_on": {
        const id = c.ad!.id;
        const off = !!s.adsOff[id];
        if (off === (c.kind === "ad_off")) return this.say(`Объявление «${c.ad!.name}» уже ${off ? "отключено" : "работает"}. Ничего не изменил.`);
        const refusal = c.kind === "ad_off" && s.ads.every((x) => x.id === id || s.adsOff[x.id]);
        if (refusal) return this.say("Это последнее объявление. Чтобы остановить показы, напишите «пауза».");
        return this.offer(c.confirm, { action: "ad", value: id });
      }
      case "undo_last": {
        const h = s.hist.find((x) => x.canUndo && !s.undone[x.id]);
        if (!h) return this.say("Отменять пока нечего.");
        return this.offer(`Понял так: отменить «${h.title}». ${h.undoNote} Применить?`, { action: "undo", value: h.id }, { label: "Открыть историю", view: "history" });
      }
      case "set_price":
      case "skip_days":
        return this.offer(c.confirm, { action: "chat", value: c });
      case "rework_ad":
        return this.say(`Переделка — на экране объявления: откройте «${c.ad!.name}» в разделе «Реклама» и нажмите «Переделать». Там видно варианты до замены.`, { label: "Открыть рекламу", view: "ads" });
      case "history":
        if (s.hist.length === 0) return this.say("Изменений пока не было: я ещё ничего не менял.");
        return this.say("В истории все изменения с причинами. Там же можно отменить.", { label: "Открыть историю", view: "history" });
      case "stats":
        return this.say(c.reply && !c.reply.startsWith("Этого я пока") ? c.reply : summary(s), { label: "Открыть статистику", view: "stats" });
      default:
        return this.say(c.reply);
    }
  }

  private say(text: string, link?: Message["link"]) {
    this.set({ msgs: [...this.state.msgs, msg("ai", text, link)] });
  }

  private offer(text: string, pending: NonNullable<State["pending"]>, link?: Message["link"]) {
    this.set({ pending, msgs: [...this.state.msgs, msg("ai", text, link)] });
  }

  override run(a: State["pending"] & object, userText?: string) {
    const msgs = userText ? [...this.state.msgs, msg("me", userText)] : this.state.msgs;
    this.set({
      pending: null,
      msgs,
      asked: a.action === "undo" ? { ...this.state.asked, [a.value]: false } : this.state.asked,
      undoing: a.action === "undo" ? { ...this.state.undoing, [a.value]: true } : this.state.undoing,
    });
    void this.finish(a, !!userText);
  }

  private async finish(a: NonNullable<State["pending"]>, fromChat: boolean) {
    let text: string;
    try {
      text = await this.perform(a);
      await this.refresh();
    } catch (error) {
      // Отказ сервиса показываем как есть: состояние на экране не меняли, значит и врать нечему.
      text = error instanceof ApiError ? error.message : "Сервис не ответил. Ничего не изменилось, попробуйте позже.";
    } finally {
      if (a.action === "undo") {
        this.set({ undoing: { ...this.state.undoing, [a.value]: false } });
      }
    }
    this.set({ msgs: [...this.state.msgs, msg(fromChat ? "ai" : "note", text)], notice: this.state.mode === "panel" ? text : "" });
    if (fromChat) this.nextRequest();
  }

  override send(raw: string) {
    const text = raw.trim();
    if (!text) return;
    const s = this.state;
    const t = text.toLowerCase();
    if (s.pending) {
      if (/(^|\s)(нет|не|отмен\S*|оставь)(\s|$)/.test(t)) {
        this.reply(text, this.queue.length ? "Хорошо, это не меняю." : "Хорошо, ничего не меняю.", { pending: null });
        return this.nextRequest();
      }
      if (/(^|\s)(да|примени\S*|подтвер\S*)(\s|$)/.test(t)) { this.set({ input: "" }); return this.run(s.pending, text); }
    }
    this.queue = [];
    this.set({ input: "", pending: null, msgs: [...s.msgs, msg("me", text)] });
    void this.understand(text);
  }
}

/** API сервиса, если экран открыт на настоящих данных; в прототипе null. */
export const liveApi = (store: Store): Api | null => (store instanceof LiveStore ? store.api : ((store as any).api as Api | null) ?? null);
