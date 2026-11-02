// Новый интерфейс на настоящих данных: сводит ответы API к состоянию экранов и шлёт действия владельца.
// Деньги приходят целыми микроединицами; в рубли переводим только здесь и только для показа.
import { ApiError } from "../api";
import type { AdLine, Alert, Api, HistoryLine, Money, Overview, Profile } from "../api";
import { ALERT } from "../cabinet/words";
import { MICROS_PER_RUBLE } from "../money";
import { leadsWord, rub } from "./data";
import type { Ad, Day, HistoryEntry, HistoryKind, Live, LiveAlert, Period, PeriodStats } from "./data";
import { budgetOf, initialState, msg, Store, week } from "./store";
import type { State } from "./store";

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MONTHS_OF = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const KIND_TITLE: Record<string, string> = { set_limit: "Изменил дневной лимит", pause: "Остановил показы", resume: "Снял паузу", pause_ad: "Отключил объявление", resume_ad: "Включил объявление", set_schedule: "Изменил время показов", set_regions: "Изменил регион показов", create_campaign: "Создал кампанию" };
const UNDOABLE = new Set(["set_limit", "resume", "pause_ad", "resume_ad", "set_schedule", "set_regions"]);
const STATUS_WORD: Record<string, string> = { planned: "Запланировано", applying: "Применяется", failed: "Не удалось", rejected: "Отклонено правилами", conflict: "Изменено кем-то ещё" };
const COMMANDS = "Через чат пока могу поставить на паузу и снять её, сменить бюджет месяца, отменить последнее изменение и рассказать цифры. Другие команды ещё не подключены, поэтому ничего не изменил.";

export const rubles = (micros: number | null | undefined) => (micros ?? 0) / MICROS_PER_RUBLE;
const rublesOrNull = (micros: number | null | undefined) => (micros === null || micros === undefined ? null : rubles(micros));
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

function toAd(line: AdLine): Ad {
  const off = line.state === "suspended";
  return { id: `${line.account}/${line.ad_id}`, name: line.title, tag: off ? "отключено" : "работает", tagTone: "neutral", leads: line.totals.conversions, cost: rubles(line.totals.cost), hook: line.title, title: line.title, text: line.text, facts: "" };
}

function kindOf(line: HistoryLine): HistoryKind {
  if (line.source === "user" || line.source === "rollback") return "yours";
  if (line.source === "experiment") return "tests";
  return line.kind === "set_limit" || line.kind === "pause" ? "money" : "ads";
}

function toEntry(line: HistoryLine, ads: Ad[]): HistoryEntry {
  const about = ads.find((a) => line.target === `ad:${a.id.split("/").pop()}`);
  const canUndo = line.ref.startsWith("change-") && line.status === "applied" && line.source !== "external" && line.source !== "rollback" && UNDOABLE.has(line.kind);
  const when = new Date(line.at).toLocaleDateString("ru-RU", { day: "numeric", month: "short", timeZone: "Europe/Moscow" });
  return {
    id: line.ref,
    kind: kindOf(line),
    when,
    title: about ? `${KIND_TITLE[line.kind] ?? "Изменил"}: «${about.name}»` : (KIND_TITLE[line.kind] ?? "Оценил результат"),
    why: line.explanation,
    next: "",
    canUndo,
    undoNote: canUndo ? "Вернётся прежнее значение. Потраченные деньги не вернутся, общий бюджет не вырастет." : undefined,
    state: canUndo ? undefined : (STATUS_WORD[line.status] ?? "Записано"),
    about: about?.id,
  };
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
  };
  return { kind: alert.kind, title: known.title, text: texts[alert.kind] ?? "Подробности в истории.", tone: known.tone === "error" ? "bad" : "warn" };
}

function profileLines(profile: Profile | null): [string, string][] {
  if (!profile) return [];
  const services = profile.services.map((x) => (x.price === null ? x.name : `${x.name}, ${rub(x.price)}`)).join("; ");
  const lines: [string, string][] = [["Чем занимаетесь", profile.niche], ["Услуги и цены", services], ["Адрес", profile.address], ["Особенности", profile.features.join(", ")], ["Сайт", profile.website]];
  return lines.filter(([, value]) => value);
}

export async function loadLive(api: Api, name: string): Promise<Partial<State>> {
  const { today } = await api.overview();
  const monthStart = `${today.slice(0, 8)}01`;
  const weekStart = dayAt(dayNumber(today) - 6);
  const trailStart = dayAt(dayNumber(today) - 13);
  const since = monthStart < trailStart ? monthStart : trailStart;
  const [overview, adsWeek, adsMonth, history, profile] = await Promise.all([
    api.overview({ since, until: today }),
    api.ads({ since: weekStart, until: today }),
    api.ads({ since: monthStart, until: today }),
    api.history(),
    api.profile().catch(() => null),
  ]);
  const month = Number(today.slice(5, 7)) - 1;
  const year = Number(today.slice(0, 4));
  const all = fillDays(overview, since, today);
  const money = overview.money[0];
  const seen = new Set(adsWeek.map((a) => a.ad_id));
  const lines = [...adsWeek, ...adsMonth.filter((a) => !seen.has(a.ad_id)).map((a) => ({ ...a, totals: { impressions: 0, clicks: 0, cost: 0, conversions: 0 } }))];
  const ads = lines.map(toAd);
  const running = overview.campaigns.filter((c) => c.state !== "archived");
  const alerts = overview.alerts.map((a) => alertOf(a, money));
  const stats: Record<Period, PeriodStats> = {
    week: periodStats("7 дней", overview, weekStart, today, adsWeek),
    month: periodStats(`С 1 ${MONTHS_OF[month]}`, overview, monthStart, today, adsMonth),
  };
  const live: Live = {
    name,
    today,
    monthName: MONTHS[month]!,
    monthOf: MONTHS_OF[month]!,
    monthDays: new Date(Date.UTC(year, month + 1, 0)).getUTCDate(),
    days: all.slice(dayNumber(monthStart) - dayNumber(since)),
    trail: all.slice(-14),
    spent: money && money.period.spent !== null ? sumMoney(overview.money, (m) => m.period.spent) : null,
    budget: sumMoney(overview.money, (m) => m.period.hard_limit),
    balance: money ? rublesOrNull(money.period.last_balance) : null,
    gap: money ? rublesOrNull(money.balance_gap) : null,
    account: money?.period.account ?? null,
    hasCampaigns: running.length > 0,
    alerts,
    stats,
    profile: profileLines(profile),
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

export class LiveStore extends Store {
  constructor(readonly api: Api, loaded: Partial<State>) {
    super({ ...initialState(), msgs: [], ...loaded });
    this.state = { ...this.state, msgs: [msg("ai", summary(this.state))] };
  }

  /** Перечитать данные после действия, которое прошло мимо журнала: профиль, решения по объявлениям. */
  async refresh() {
    this.set(await loadLive(this.api, this.state.live?.name ?? ""));
  }

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
        const [account, adId] = a.value.split("/");
        const active = !!s.adsOff[a.value];
        const name = s.ads.find((x) => x.id === a.value)?.name ?? "";
        const { status } = await this.api.setAdState(account!, adId!, active, crypto.randomUUID());
        if (status === "pending") return `Запрос про «${name}» записан, Яндекс ещё применяет его.`;
        return `${active ? "Включил" : "Отключил"} «${name}». Общий бюджет не изменился. Запись в истории.`;
      }
      case "undo": {
        const h = s.hist.find((x) => x.id === a.value);
        const { status } = await this.api.rollback(Number(a.value.replace("change-", "")));
        if (status === "pending") return "Яндекс не ответил, отмена записана и будет повторена.";
        this.set({ undone: { ...this.state.undone, [a.value]: true } });
        return `Отменил: ${h?.title ?? "изменение"}. Исходная запись осталась в истории.`;
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
      case "balance":
        await this.api.confirmBalance(s.live!.account!);
        return "Сверка начнётся заново от нынешнего баланса. Показы сами не включатся.";
      default:
        return "Это действие ещё не подключено к сервису, ничего не изменилось.";
    }
  }

  override run(a: State["pending"] & object, userText?: string) {
    const msgs = userText ? [...this.state.msgs, msg("me", userText)] : this.state.msgs;
    this.set({ pending: null, msgs, asked: a.action === "undo" ? { ...this.state.asked, [a.value]: false } : this.state.asked });
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
    }
    this.set({ msgs: [...this.state.msgs, msg(fromChat ? "ai" : "note", text)], notice: this.state.mode === "panel" ? text : "" });
  }

  override send(raw: string) {
    const text = raw.trim();
    if (!text) return;
    const s = this.state;
    const t = text.toLowerCase();
    if (s.pending) {
      if (/(^|\s)(нет|не|отмен\S*|оставь)(\s|$)/.test(t)) return this.reply(text, "Хорошо, ничего не меняю.", { pending: null });
      if (/(^|\s)(да|примени\S*|подтвер\S*)(\s|$)/.test(t)) { this.set({ input: "" }); return this.run(s.pending, text); }
    }
    if (s.paused && /включ|сними|возобнов/.test(t)) {
      return this.reply(text, "Понял так: снять паузу. Перед включением проверю, что расход не выйдет за бюджет. Применить?", { pending: { action: "pause" } });
    }
    const amount = Number((t.match(/\d[\d\s]*/) ?? ["0"])[0].replace(/\s/g, ""));
    if (/бюджет/.test(t) && amount >= 1000) {
      const spent = s.live?.spent ?? 0;
      if (amount < spent) return this.reply(text, `В этом месяце уже потрачено ${rub(spent)}, бюджет не может быть меньше.`);
      const lower = amount < budgetOf(s);
      return this.reply(text, `Понял так: бюджет ${rub(amount)} в месяц.${lower ? " Сначала снижу лимиты в Яндексе, потом сменю бюджет." : ""} Применить?`, { pending: { action: "setting", value: ["budget", amount] } });
    }
    if (/пауз|останов/.test(t)) {
      if (s.paused) return this.reply(text, "Реклама уже на паузе. Чтобы включить, напишите «включи».");
      return this.reply(text, "Понял так: остановить все показы. Настройки сохранятся. Применить?", { pending: { action: "pause" } });
    }
    if (/отмени/.test(t)) {
      const h = s.hist.find((x) => x.canUndo && !s.undone[x.id]);
      if (!h) return this.reply(text, "Отменять пока нечего.");
      return this.reply(text, `Понял так: отменить «${h.title}». ${h.undoNote} Применить?`, { pending: { action: "undo", value: h.id } }, { label: "Открыть историю", view: "history" });
    }
    if (/истор|что (ты )?(делал|сделал|менял)/.test(t)) return this.reply(text, "В истории все изменения с причинами. Там же можно отменить.", {}, { label: "Открыть историю", view: "history" });
    if (/дела|итог|статист|цифр|цен|дорог|как/.test(t)) return this.reply(text, summary(s), {}, { label: "Открыть статистику", view: "stats" });
    this.reply(text, COMMANDS);
  }
}

/** API сервиса, если экран открыт на настоящих данных; в прототипе null. */
export const liveApi = (store: Store): Api | null => (store instanceof LiveStore ? store.api : null);
