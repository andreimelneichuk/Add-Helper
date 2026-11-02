import { useSyncExternalStore } from "react";
import { ADS, DAYS, FACTS, histFor, leadsWord, MIN_BUDGET, OPENING, OPTIONS, rub, STEPS } from "./data";
import type { Ad, Fact, HistoryEntry, HistoryKind, Period, SettingKey, Situation, View } from "./data";

export type Who = "me" | "ai" | "note";
export type Message = { id: number; who: Who; text: string; link?: { label: string; view: View } };
type Pending =
  | { action: "pause" }
  | { action: "setting"; value: [SettingKey, string | number] }
  | { action: "undo"; value: string }
  | { action: "price"; value: number }
  | { action: "ad"; value: string }
  | { action: "adSave"; value: Ad };
type Action = Pending | { action: "clients"; value: number };

export type State = {
  stage: "work" | "start";
  situation: Situation;
  mode: "panel" | "chat";
  view: View;
  /** Итог действия на панели; в чате итог виден в ленте. */
  notice: string;
  price: number | null;
  step: number;
  input: string;
  pending: Pending | null;
  msgs: Message[];
  hist: HistoryEntry[];
  undone: Record<string, boolean>;
  asked: Record<string, boolean>;
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
  factEdit: { id: string | null; value: string } | null;
};

let nextId = 1;
const newFact = (value: string): Fact => ({ id: "n" + nextId++, label: "Вы добавили", value, source: "от вас" });
const msg = (who: Who, text: string, link?: Message["link"]): Message => ({ id: nextId++, who, text, link });

const GREETING = "Здравствуйте! Я веду рекламу малого бизнеса сам: создаю, слежу за деньгами, улучшаю. Начнём с вашего сайта. Напишите ссылку здесь или откройте «Настройку» и заполните всё там.";

export function initialState(stage: State["stage"] = "work", situation: Situation = "running"): State {
  return {
    stage, situation, mode: "panel", view: "overview", notice: "", price: null, step: 0, input: "", pending: null,
    msgs: [msg("ai", stage === "start" ? GREETING : OPENING[situation])],
    hist: histFor(situation), undone: {}, asked: {}, adsOff: {}, ads: ADS, openAd: null, paused: false, topped: false, decided: null, extra: 0,
    metric: "cost", selDay: null, period: "week", filter: "all", clientsInput: "", clients: null,
    f: { services: "маникюр и педикюр", area: "Сокольники", hours: "в часы работы", goal: 400, budget: 20000 },
    site: "", codeCopied: false, inn: "", yandex: false, metrica: false, block: "", draftOff: {}, budgetDraft: null, facts: FACTS, factEdit: null,
  };
}

/** Расход и обращения за 7 дней и за 7 дней до них. */
export function week(s: State) {
  const d = DAYS[s.situation];
  const sum = (a: typeof d, i: 0 | 1) => a.reduce((x, y) => x + y[i], 0);
  const last = d.slice(-7);
  const prev = d.slice(-14, -7);
  return { cost: sum(last, 0), leads: sum(last, 1), prevCost: sum(prev, 0), prevLeads: sum(prev, 1) };
}
export const budgetOf = (s: State) => Number(s.f.budget) + s.extra;
export const spentOf = (s: State) => DAYS[s.situation].reduce((x, d) => x + d[0], 0);
export const stopped = (s: State) => s.situation === "problem" && !s.topped;
export const warned = (s: State) => s.situation === "budget" && !s.decided;
export const statusText = (s: State) =>
  s.paused ? "На паузе" : stopped(s) ? "Показы остановлены" : warned(s) ? "Бюджет на исходе" : s.situation === "problem" ? "Жду деньги"
    : s.situation === "access" && !s.topped ? "Нет доступа" : s.situation === "first" ? "Собираю данные" : "Реклама идёт";
/** Название объявления с учётом новой цены из профиля. */
export const adName = (s: State, id: string) => (s.price && id === "a2" ? `Маникюр от ${rub(s.price)}` : s.ads.find((a) => a.id === id)!.name);
/** Что написано на объявлении: подставная цена не должна затирать уже переделанный текст. */
export function adShown(s: State, ad: Ad) {
  const priced = !!s.price && ad.id === "a2" && ad.hook === ADS[1]!.hook;
  return { hook: priced ? adName(s, ad.id) : ad.hook, title: priced ? adName(s, ad.id) : ad.title };
}
export function adStat(s: State, ad: Ad) {
  if (s.adsOff[ad.id]) return "Отключено";
  // В первые дни показатели карточки ещё не накоплены: суммы из данных относятся к работающей рекламе.
  if (s.situation === "first" || ad.leads <= 0) return "Собираю данные";
  return `${ad.leads} ${leadsWord(ad.leads)}, по ${rub(ad.cost / ad.leads)}`;
}

type Template = { needs: string[]; hook: (s: State) => string; title: (s: State) => string; text: string; facts: string };
type Topic = "manicure" | "pedicure" | "promo";
const manicurePrice = (s: State) => rub(s.price ?? 1200);
// Заглушка на месте языковой модели: варианты собраны только из фактов профиля, needs говорит, из каких.
const TEMPLATES: Record<Topic, Template[]> = {
  manicure: [
    { needs: ["where", "hours"], hook: () => "Маникюр у метро Сокольники", title: () => "Маникюр рядом с домом", text: "Открыты каждый день до 21:00. Запишитесь онлайн.", facts: "Адрес и часы из профиля" },
    { needs: ["prices", "where"], hook: (s) => `Маникюр от ${manicurePrice(s)}`, title: (s) => `Маникюр от ${manicurePrice(s)} у метро Сокольники`, text: "Запишитесь онлайн на удобное время.", facts: "Цена и адрес из профиля" },
    { needs: ["what"], hook: () => "Аккуратный маникюр", title: () => "Запишитесь на маникюр", text: "Свободное время видно сразу при записи.", facts: "Услуга из профиля" },
  ],
  pedicure: [
    { needs: ["where", "hours"], hook: () => "Педикюр у метро Сокольники", title: () => "Педикюр рядом с домом", text: "Открыты каждый день до 21:00. Запишитесь онлайн.", facts: "Адрес и часы из профиля" },
    { needs: ["prices", "where"], hook: () => "Педикюр от 1\u00a0800\u00a0₽", title: () => "Педикюр от 1\u00a0800\u00a0₽ у метро Сокольники", text: "Запишитесь онлайн на удобное время.", facts: "Цена и адрес из профиля" },
    { needs: ["what"], hook: () => "Педикюр без спешки", title: () => "Запишитесь на педикюр", text: "Свободное время видно сразу при записи.", facts: "Услуга из профиля" },
  ],
  promo: [
    { needs: ["promo", "where"], hook: () => "Минус 20% на первый визит", title: () => "Скидка 20% у метро Сокольники", text: "Для тех, кто приходит к нам впервые. Запишитесь онлайн.", facts: "Скидка и адрес из профиля" },
    { needs: ["promo", "hours"], hook: () => "Первый визит на 20% дешевле", title: () => "Скидка 20% на первый визит", text: "Открыты каждый день до 21:00.", facts: "Скидка и часы из профиля" },
  ],
};
const amounts = (text: string) => [...text.matchAll(/(\d[\d\s]*?)\s*(%|₽|руб|процент)/gi), ...text.matchAll(/скидк\S*\s+(\d+)()/gi)].map((m) => ({ num: m[1]!.replace(/\s/g, ""), shown: m[0].trim() }));
/** Чего из просьбы нельзя писать: реклама говорит только то, что подтверждено профилем. */
function askRefusal(s: State, ask: string): string | null {
  const known = new Set(amounts([...s.facts.map((f) => f.value), s.price ? rub(s.price) : ""].join(" ")).map((a) => a.num));
  const unknown = amounts(ask).find((a) => !known.has(a.num));
  if (unknown) return `В профиле нет «${unknown.shown}». Добавьте это в профиль, и я смогу написать об этом в рекламе.`;
  if (/скидк|акци/i.test(ask) && !s.facts.some((f) => f.id === "promo")) return "В профиле нет акции. Добавьте её в профиль, и я смогу написать о ней в рекламе.";
  if (/отзыв/i.test(ask)) return "Отзыв можно использовать только с подтверждённым согласием клиента. Пока его нет, напишу без отзыва.";
  if (/лучш|№\s*1|гарантир/i.test(ask)) return "Такое обещание нечем подтвердить, а закон о рекламе требует подтверждения. Напишу о том, что есть в профиле.";
  return null;
}
const topicOf = (text: string): Topic | null => (/педикюр/i.test(text) ? "pedicure" : /маникюр/i.test(text) ? "manicure" : /акци|скидк/i.test(text) ? "promo" : null);

/** Новый вариант по просьбе; base null у нового объявления. В показы он сам не идёт. */
export function draftAd(s: State, base: Ad | null, ask: string, attempt = 0): { ad: Ad } | { refusal: string } {
  const refusal = askRefusal(s, ask);
  if (refusal) return { refusal };
  if (base && /короче/i.test(ask)) return { ad: { ...base, text: base.text.split(/(?<=\.)\s/)[0]! } };
  const topic = topicOf(ask) ?? (base ? topicOf(`${base.name} ${base.title}`) : null) ?? "manicure";
  const have = new Set(s.facts.map((f) => f.id));
  const fit = TEMPLATES[topic].filter((t) => t.needs.every((n) => have.has(n)) && t.hook(s) !== base?.hook);
  const t = fit[attempt % Math.max(1, fit.length)];
  if (!t) return { refusal: "В профиле пока мало фактов для такого объявления. Добавьте услуги, цены или адрес." };
  const content = { hook: t.hook(s), title: t.title(s), text: t.text, facts: t.facts };
  return { ad: base ? { ...base, ...content } : { id: "n" + nextId++, name: content.hook, tag: "новое", tagTone: "accent", leads: 0, cost: 0, ...content } };
}

const shown = (v: string | number) => (typeof v === "number" ? rub(v) : v);
/** Обе половины идущей проверки вариантов. */
export const inTest = (s: State, id: string) => s.situation === "running" && (id === "a1" || id === "a2");

/** Почему действие нельзя выполнить; общая проверка для панели и чата. */
function refusalOf(s: State, a: Pending): string | null {
  if (a.action === "setting" && a.value[0] === "budget" && Number(a.value[1]) + s.extra < spentOf(s)) return `В ноябре уже потрачено ${rub(spentOf(s))}, бюджет месяца не может быть меньше.`;
  if (a.action === "ad" && !s.adsOff[a.value] && s.ads.every((x) => x.id === a.value || s.adsOff[x.id])) return "Это последнее объявление. Чтобы остановить показы, нажмите «Пауза».";
  return null;
}

/** Что изменится: показываем до применения. */
export function confirmText(s: State, a: Pending): string {
  switch (a.action) {
    case "pause": return s.paused ? "Включить рекламу снова?" : "Остановить все показы? Настройки сохранятся.";
    case "setting": {
      const [key, val] = a.value;
      return key === "budget"
        ? `Бюджет ${rub(Number(val))} в месяц. Сначала обновлю лимит в Яндексе, потом перераспределю деньги. Отменить можно в истории.`
        : `${OPTIONS[key].label}: ${shown(val)}. Общий бюджет не изменится. Отменить можно в истории.`;
    }
    case "undo": { const h = s.hist.find((x) => x.id === a.value); return `Отменить «${h?.title}»? ${h?.undoNote ?? ""}`; }
    case "price": return `Маникюр теперь от ${rub(a.value)}. Обновлю профиль и объявление со старой ценой.`;
    case "ad": return `Отключить «${adName(s, a.value)}»? ${inTest(s, a.value) ? "Идёт проверка вариантов, она закончится раньше срока. " : ""}Его деньги перераспределю между остальными. Отменить можно в истории.`;
    case "adSave": {
      if (!s.ads.some((x) => x.id === a.value.id)) return `Добавить объявление «${a.value.name}»? Яндекс проверит его, обычно до суток. Общий бюджет не вырастет: деньги поделю между объявлениями. Отключить можно в истории.`;
      return `Поставить новый вариант «${adName(s, a.value.id)}»? Старый работает, пока Яндекс проверяет новый, обычно до суток. ${inTest(s, a.value.id) ? "Идёт проверка вариантов, она закончится раньше срока. " : ""}Отменить можно в истории.`;
    }
  }
}

export class Store {
  state: State;
  private listeners = new Set<() => void>();
  constructor(state: State = initialState()) { this.state = state; }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getSnapshot = () => this.state;
  set = (patch: Partial<State>) => { this.state = { ...this.state, ...patch }; this.listeners.forEach((fn) => fn()); };

  private entry(title: string, why: string, next: string, kind: HistoryKind, undo?: { note: string; revert: NonNullable<HistoryEntry["revert"]> }): HistoryEntry {
    return { id: "n" + nextId++, kind, when: "Сейчас", title, why, next, canUndo: !!undo, undoNote: undo?.note, revert: undo?.revert, state: "Записано" };
  }
  /** Новая правка того же объекта закрывает отмену старой: иначе отмена вернула бы устаревшее значение. */
  private closed(same: (h: HistoryEntry) => boolean): HistoryEntry[] {
    return this.state.hist.map((h) => (h.canUndo && !this.state.undone[h.id] && same(h) ? { ...h, canUndo: false, state: "Изменено позже" } : h));
  }
  // Единая точка изменений: и чат, и панель приходят сюда, запись всегда попадает в историю.
  private apply(a: Action): Partial<State> & { text: string } {
    const s = this.state;
    switch (a.action) {
      case "pause": {
        const paused = !s.paused;
        return { paused, hist: [this.entry(paused ? "Поставил рекламу на паузу" : "Включил рекламу", "Вы попросили.", "Настройки сохранены.", "yours"), ...s.hist], text: paused ? "Поставил на паузу. Показы остановятся примерно через 5 минут, настройки сохранены." : "Включил. Показы начнутся примерно через 5 минут." };
      }
      case "setting": {
        const [key, val] = a.value;
        const label = OPTIONS[key].label;
        const undo = { note: `Вернётся прежнее значение: ${shown(s.f[key])}.`, revert: { setting: [key, s.f[key]] as [SettingKey, string | number] } };
        return { f: { ...s.f, [key]: val }, hist: [this.entry(`Изменил настройку. ${label}: ${shown(val)}`, "Вы попросили.", key === "budget" ? "Лимит в Яндексе обновлён." : "Бюджет не изменился.", key === "budget" ? "money" : "yours", undo), ...this.closed((h) => h.revert?.setting?.[0] === key)], text: `Готово. ${label}: ${shown(val)}. Отменить можно в истории.` };
      }
      case "undo": {
        const h = s.hist.find((x) => x.id === a.value)!;
        const back = h.revert?.setting;
        return { ...(back ? { f: { ...s.f, [back[0]]: back[1] } } : {}), ...(h.revert?.ad ? { adsOff: { ...s.adsOff, [h.revert.ad]: false } } : {}), ...(h.revert?.adOff ? { adsOff: { ...s.adsOff, [h.revert.adOff]: true } } : {}), ...(h.revert?.adContent ? { ads: s.ads.map((x) => (x.id === h.revert!.adContent!.id ? h.revert!.adContent! : x)) } : {}), undone: { ...s.undone, [a.value]: true }, asked: { ...s.asked, [a.value]: false }, hist: [this.entry(`Отменил: ${h.title}`, "Вы попросили.", "Исходная запись осталась в истории.", "yours"), ...s.hist], text: `Отменил: ${h.title}. Общий бюджет не изменился.` };
      }
      case "ad": {
        const name = adName(s, a.value);
        const off = !s.adsOff[a.value];
        const undo = off ? { note: "Объявление снова включится и разделит бюджет с другими. Общий бюджет не вырастет.", revert: { ad: a.value } } : undefined;
        return { adsOff: { ...s.adsOff, [a.value]: off }, hist: [this.entry(`${off ? "Отключил" : "Включил"} «${name}»`, "Вы попросили.", "Общий бюджет не изменился.", "ads", undo), ...this.closed((h) => h.revert?.ad === a.value)], text: `${off ? "Отключил" : "Включил"} «${name}». ${off ? "Его деньги перераспределю между остальными. Отменить можно в истории." : "Оно снова делит бюджет с другими."}` };
      }
      case "adSave": {
        const old = s.ads.find((x) => x.id === a.value.id);
        if (!old) {
          const undo = { note: "Объявление отключится, но останется в списке. Общий бюджет не изменится.", revert: { adOff: a.value.id } };
          const entry = { ...this.entry(`Добавил объявление «${a.value.name}»`, "Вы попросили.", "Яндекс проверяет его, обычно до суток. Общий бюджет не изменился.", "ads", undo), about: a.value.id };
          return { ads: [...s.ads, a.value], openAd: s.openAd === "new" ? a.value.id : s.openAd, hist: [entry, ...s.hist], text: `Добавил объявление «${a.value.name}». Яндекс проверит его, обычно до суток. Общий бюджет не вырос.` };
        }
        const name = adName(s, old.id);
        const undo = { note: "Вернётся прежний вариант, Яндекс проверит его заново. Общий бюджет не изменится.", revert: { adContent: old } };
        const entry = { ...this.entry(`Поставил новый вариант «${name}»`, "Вы попросили.", "Старый работает, пока Яндекс проверяет новый, обычно до суток.", "ads", undo), about: old.id };
        return { ads: s.ads.map((x) => (x.id === old.id ? a.value : x)), hist: [entry, ...this.closed((h) => h.about === old.id)], text: `Отправил новый вариант «${name}» на проверку Яндексу, обычно до суток. Старый пока работает. Отменить можно в истории.` };
      }
      case "price":
        return { price: a.value, hist: [this.entry(`Обновил цену маникюра: от ${rub(a.value)}`, "Вы сообщили об изменении.", "Исправил объявление, в котором была старая цена. Яндекс проверит его заново.", "ads"), ...s.hist], text: `Обновил профиль и объявление: маникюр от ${rub(a.value)}. Яндекс проверит его заново, обычно до суток.` };
      case "clients": {
        const w = week(s);
        return { clients: a.value, text: `Записал ${a.value} из ${w.leads}. Клиент обошёлся в ${rub(w.cost / a.value)}.` };
      }
    }
  }
  /** Выполнить действие: с панели без userText, из чата с текстом человека. */
  run(a: Action, userText?: string) {
    const { text, ...patch } = this.apply(a);
    const msgs = [...this.state.msgs];
    if (userText) msgs.push(msg("me", userText));
    msgs.push(msg(userText ? "ai" : "note", text));
    this.set({ ...patch, msgs, pending: null, notice: this.state.mode === "panel" ? text : "" });
  }
  /** Запросить подтверждение с панели: то же состояние, что у вопроса «Применить?» в чате. */
  ask(a: Pending) {
    const refusal = refusalOf(this.state, a);
    this.set(refusal ? { notice: refusal, pending: null } : { pending: a, notice: "" });
  }
  confirm = () => { if (this.state.pending) this.run(this.state.pending); };
  decline = () => this.set({ pending: null });
  goView = (view: View) => this.set({ view, mode: "panel", openAd: null });
  openAdScreen = (id: string) => this.set({ view: "ads", mode: "panel", openAd: id, notice: "" });
  // Вопрос с панели не переносим в чат: там он не был задан словами.
  openChat = (input = "") => this.set({ mode: "chat", notice: "", input, pending: this.state.mode === "panel" ? null : this.state.pending });
  pickSituation = (situation: Situation) => this.set({ situation, mode: "panel", view: "overview", selDay: null, topped: false, decided: null, extra: 0, pending: null, notice: "", clients: null, undone: {}, asked: {}, adsOff: {}, ads: ADS, openAd: null, price: null, hist: histFor(situation), msgs: [msg("ai", OPENING[situation])] });
  /** Отметка на панели и ответ ИИ для решений в баннере внимания. */
  private said(note: string, ai: string, patch: Partial<State>) { this.set({ ...patch, msgs: [...this.state.msgs, msg("note", note), msg("ai", ai)] }); }
  reportTopUp = () => this.said("Вы сообщили о пополнении", "Проверяю счёт. Как только деньги дойдут, включу показы, обычно за 5 минут.", { topped: true });
  reconnect = () => this.said("Вы подключили кабинет заново", "Доступ вернулся. Сверил кабинет с журналом: всё совпадает, продолжаю вести рекламу.", { topped: true });
  addBudget = () => this.said("Вы добавили 3 000 ₽", "Поднял лимит ноября до 23 000 ₽. Показы идут до конца месяца.", { extra: 3000, decided: "added", hist: [this.entry("Добавил 3 000 ₽ к бюджету ноября", "Вы решили.", "Новый бюджет 23 000 ₽.", "money"), ...this.state.hist] });
  keepBudget = () => this.said("Вы оставили бюджет как есть", "Хорошо. Напомню, когда показы остановятся.", { decided: "kept" });

  private reply(userText: string, aiText: string, patch: Partial<State> = {}, link?: Message["link"]) {
    this.set({ ...patch, input: "", msgs: [...this.state.msgs, msg("me", userText), msg("ai", aiText, link)] });
  }
  // Разбор фраз в закрытый набор действий: заглушка на месте языковой модели. Менять можно только после подтверждения.
  send(raw: string) {
    const text = raw.trim();
    if (!text) return;
    const s = this.state;
    const t = text.toLowerCase();
    if (s.stage === "start") return this.sendStart(text, t);
    if (s.pending) {
      // Отказ проверяем первым и по целым словам: «не применяй» не должно применять.
      if (/(^|\s)(нет|не|отмен\S*|оставь)(\s|$)/.test(t)) return this.reply(text, "Хорошо, ничего не меняю.", { pending: null });
      if (/(^|\s)(да|примени\S*|подтвер\S*)(\s|$)/.test(t)) { this.set({ input: "" }); return this.run(s.pending, text); }
    }
    const num = Number((t.match(/\d[\d\s]*/) || ["0"])[0].replace(/\s/g, ""));
    if (/нов\S* объявлен|(сделай|добавь|создай) объявлен/.test(t)) return this.offerDraft(text, draftAd(s, null, text));
    if (/передел|перепиш|короче/.test(t)) {
      const ad = /педикюр/.test(t) ? s.ads.find((x) => x.id === "a3") : /скидк/.test(t) ? s.ads.find((x) => x.id === "a1") : /маникюр/.test(t) ? s.ads.find((x) => x.id === "a2") : null;
      if (!ad) return this.reply(text, "Какое объявление переделать: со скидкой, маникюр или педикюр? Можно и открыть его в рекламе.", {}, { label: "Открыть рекламу", view: "ads" });
      return this.offerDraft(text, draftAd(s, ad, text));
    }
    if (/пауз|останов/.test(t)) return this.reply(text, s.paused ? "Понял так: включить рекламу снова. Применить?" : "Понял так: остановить все показы. Настройки сохранятся. Применить?", { pending: { action: "pause" } });
    if (/включ/.test(t) && s.paused) return this.reply(text, "Понял так: включить рекламу снова. Применить?", { pending: { action: "pause" } });
    if (/воскрес/.test(t)) return this.reply(text, "Понял так: не показывать рекламу по воскресеньям. Бюджет не изменится. Применить?", { pending: { action: "setting", value: ["hours", "кроме воскресений"] } });
    if (/бюджет|трат/.test(t) && num >= 1000) {
      const refusal = refusalOf(s, { action: "setting", value: ["budget", num] });
      if (refusal) return this.reply(text, refusal, { pending: null });
    }
    if (/бюджет|трат/.test(t) && num >= 1000) return this.reply(text, `Понял так: бюджет ${rub(num)} в месяц. Сначала обновлю лимит в Яндексе, потом перераспределю деньги. Применить?`, { pending: { action: "setting", value: ["budget", num] } });
    if (/клиент/.test(t) && num > 0 && num <= week(s).leads) { this.set({ input: "" }); return this.run({ action: "clients", value: num }, text); }
    if (/отмени/.test(t)) {
      const h = s.hist.find((x) => x.canUndo && !s.undone[x.id]);
      if (!h) return this.reply(text, "Отменять пока нечего.");
      return this.reply(text, `Понял так: отменить «${h.title}». ${h.undoNote} Применить?`, { pending: { action: "undo", value: h.id } }, { label: "Открыть историю", view: "history" });
    }
    if (/истор|что (ты )?(делал|сделал|менял)/.test(t)) return this.reply(text, "В истории все изменения с причинами. Там же можно отменить.", {}, { label: "Открыть историю", view: "history" });
    if (/новая цена|цена теперь|подорожал|акци/.test(t) && num >= 100) return this.reply(text, `Понял так: маникюр теперь от ${rub(num)}. Обновлю профиль и объявление со старой ценой. Применить?`, { pending: { action: "price", value: num } });
    if (/дорог|цен|почему/.test(t)) { const w = week(s); return this.reply(text, `Обращение за 7 дней стоит ${rub(w.cost / Math.max(1, w.leads))}, ваша цель ${rub(Number(s.f.goal))}. Дороже всего «${adName(s, "a2")}», 590 ₽: его я сейчас проверяю.`, {}, { label: "Открыть статистику", view: "stats" }); }
    if (/объявлен|реклам|текст/.test(t)) return this.reply(text, "Что поменять? Могу сделать отсюда, или откройте объявления и настройки.", {}, { label: "Открыть рекламу", view: "ads" });
    if (/дела|итог|статист|как/.test(t)) { const w = week(s); return this.reply(text, `За 7 дней ${w.leads} обращений по ${rub(w.cost / Math.max(1, w.leads))}, неделей раньше ${w.prevLeads}.`, {}, { label: "Открыть обзор", view: "overview" }); }
    this.reply(text, "Этого я пока не умею. Могу поставить на паузу, изменить бюджет, район или время показов, переделать объявление или сделать новое, отменить действие, объяснить цифры.");
  }
  private offerDraft(text: string, made: ReturnType<typeof draftAd>) {
    if ("refusal" in made) return this.reply(text, made.refusal, { pending: null });
    const { hook, text: body, facts } = made.ad;
    this.reply(text, `Понял так: ${hook}. ${body} Факты: ${facts.toLowerCase()}. Общий бюджет не вырастет. Применить?`, { pending: { action: "adSave", value: made.ad } });
  }
  private sendStart(text: string, t: string) {
    const s = this.state;
    if (s.step === 0) { this.set({ site: s.site || text, input: "", step: 1, block: "", msgs: [...s.msgs, msg("me", text), msg("ai", STEPS[0]!.ai)] }); return; }
    if (s.step === 1 && !/верно|(^|\s)да(\s|$)|дальше/.test(t)) return this.reply(text, "Записал в профиль. Только такие факты попадут в рекламу.", { facts: [...s.facts, newFact(text)] });
    if (s.step === 2) { const n = Number((t.match(/\d[\d\s]*/) || ["0"])[0].replace(/\s/g, "")); if (n >= 1000) return this.reply(text, `Поставил ${rub(n)} в месяц.`, { f: { ...s.f, budget: n } }); }
    if (/верно|(^|\s)да(\s|$)|дальше|нравится|запуск|продолж/.test(t)) { this.set({ input: "" }); return this.next(text); }
    this.reply(text, "Понял. Когда будете готовы, напишите «дальше» или нажмите кнопку на вкладке «Настройка».");
  }
  /** Сохранить правку факта: в рекламу попадают только подтверждённые человеком значения. */
  saveFact = () => {
    const { factEdit: edit, facts } = this.state;
    const value = edit?.value.trim();
    if (!edit || !value) return;
    const next = edit.id === null ? [...facts, newFact(value)] : facts.map((f) => (f.id === edit.id ? { ...f, value, source: "от вас" } : f));
    this.set({ facts: next, factEdit: null, block: "" });
  };
  removeFact = (id: string) => this.set({ facts: this.state.facts.filter((f) => f.id !== id), factEdit: null, block: "" });
  /** Следующий шаг запуска; держит на шаге, пока не выполнены условия. */
  next(userText?: string) {
    const s = this.state;
    if (s.factEdit) return this.set({ block: "Сохраните или отмените правку факта." });
    if (s.step === 2 && s.budgetDraft !== null && Number(s.budgetDraft) < MIN_BUDGET) return this.set({ block: `Укажите сумму от ${rub(MIN_BUDGET)} в месяц.` });
    if (s.step === 0 && !s.site.trim()) return this.set({ block: "Вставьте ссылку или расскажите о бизнесе в чате." });
    if (s.step >= 3 && s.ads.every((a) => s.draftOff[a.id])) return this.set({ block: "Оставьте хотя бы одно объявление." });
    if (s.step === 4 && !s.yandex) return this.set({ block: "Подключите кабинет Яндекса." });
    if (s.step === 5 && !/^(\d{10}|\d{12})$/.test(s.inn)) return this.set({ block: "ИНН это 10 или 12 цифр." });
    if (s.step === 6 && !s.metrica) return this.set({ block: "Проверьте установку кода." });
    const msgs = [...s.msgs];
    if (userText) msgs.push(msg("me", userText));
    if (s.step === STEPS.length - 1) {
      msgs.push(msg("ai", "Запустил. Яндекс проверяет объявления, лимит поставлен в самом кабинете.\n\nПрототип: перематываю на 14 ноября, как будто прошло две недели."), msg("ai", OPENING.running));
      return this.set({ stage: "work", mode: "panel", view: "overview", msgs, block: "" });
    }
    msgs.push(msg("ai", STEPS[s.step]!.ai));
    this.set({ step: s.step + 1, msgs, block: "" });
  }
}

export function useStoreState(store: Store): State {
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}
