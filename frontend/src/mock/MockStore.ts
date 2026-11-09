// Прототип: магазин на подставных данных (`/?mock` и тесты). В боевую сборку не входит.
import { rub } from "../app/data";
import type { HistoryEntry, HistoryKind, SettingKey, Situation } from "../app/data";
import { fixtures } from "../app/fixtures";
import { adName, budgetOf, draftAd, msg, refusalOf, shown, spentOf, Store, week } from "../app/store";
import type { Action, State } from "../app/store";

let nextId = 1000;

export class MockStore extends Store {
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
      case "balance": return { text: "" };
      case "chat":
      case "test": return { text: "Это действие работает только на настоящих данных." };
      case "adPublish": return { text: "Публикация работает только в настоящем кабинете Директа." };
      case "pause": {
        const paused = !s.paused;
        return { paused, hist: [this.entry(paused ? "Поставил рекламу на паузу" : "Включил рекламу", "Вы попросили.", "Настройки сохранены.", "yours"), ...s.hist], text: paused ? "Поставил на паузу. Показы остановятся примерно через 5 минут, настройки сохранены." : "Включил. Показы начнутся примерно через 5 минут." };
      }
      case "setting": {
        const [key, val] = a.value;
        const label = fixtures.OPTIONS[key].label;
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
      case "adArchive": {
        const name = adName(s, a.value);
        return {
          ads: s.ads.map((x) => (x.id === a.value ? { ...x, tag: "в архиве", tagTone: "neutral" as const } : x)),
          openAd: null,
          hist: [this.entry(`Отправил «${name}» в архив`, "Вы попросили.", "В любой момент можно вернуть из архива.", "ads"), ...s.hist],
          text: `Отправил «${name}» в архив.`,
        };
      }
      case "adRestore": {
        const name = adName(s, a.value);
        const nextOff = { ...s.adsOff };
        delete nextOff[a.value];
        return {
          ads: s.ads.map((x) => (x.id === a.value ? { ...x, tag: "готово к запуску", tagTone: "accent" as const } : x)),
          adsOff: nextOff,
          openAd: null,
          hist: [this.entry(`Вернул «${name}» из архива`, "Вы попросили.", "Объявление снова в списке.", "ads"), ...s.hist],
          text: `Вернул «${name}» из архива.`,
        };
      }
      case "price":
        return { price: a.value, hist: [this.entry(`Обновил цену маникюра: от ${rub(a.value)}`, "Вы сообщили об изменении.", "Исправил объявление, в котором была старая цена. Яндекс проверит его заново.", "ads"), ...s.hist], text: `Обновил профиль и объявление: маникюр от ${rub(a.value)}. Яндекс проверит его заново, обычно до суток.` };
      case "clients": {
        const w = week(s);
        const costPart = a.value > 0 ? `Клиент обошёлся в ${rub(w.cost / a.value)}.` : "Клиентов пока нет.";
        return { clients: a.value, text: `Записал ${a.value} из ${w.leads}. ${costPart}` };
      }
    }
  }
  /** Выполнить действие: с панели без userText, из чата с текстом человека. */
  override run(a: Action, userText?: string): void {
    const { text, ...patch } = this.apply(a);
    const msgs = [...this.state.msgs];
    if (userText) msgs.push(msg("me", userText));
    msgs.push(msg(userText ? "ai" : "note", text));
    this.set({ ...patch, msgs, pending: null, notice: this.state.mode === "panel" ? text : "" });
  }
  override pickSituation = (situation: Situation) => this.set({ situation, mode: "panel", view: "overview", selDay: null, topped: false, decided: null, extra: 0, pending: null, notice: "", clients: null, undone: {}, asked: {}, adsOff: {}, ads: fixtures.ADS, openAd: null, price: null, hist: fixtures.histFor(situation), msgs: [msg("ai", fixtures.OPENING[situation])] });
  /** Отметка на панели и ответ ИИ для решений в баннере внимания. */
  private said(note: string, ai: string, patch: Partial<State>) { this.set({ ...patch, msgs: [...this.state.msgs, msg("note", note), msg("ai", ai)] }); }
  override reportTopUp = () => this.said("Вы сообщили о пополнении", "Проверяю счёт. Как только деньги дойдут, включу показы, обычно за 5 минут.", { topped: true });
  override reconnect = () => this.said("Вы подключили кабинет заново", "Доступ вернулся. Сверил кабинет с журналом: всё совпадает, продолжаю вести рекламу.", { topped: true });
  override addBudget = () => this.said("Вы добавили 3 000 ₽", "Поднял лимит ноября до 23 000 ₽. Показы идут до конца месяца.", { extra: 3000, decided: "added", hist: [this.entry("Добавил 3 000 ₽ к бюджету ноября", "Вы решили.", "Новый бюджет 23 000 ₽.", "money"), ...this.state.hist] });
  override keepBudget = () => this.said("Вы оставили бюджет как есть", "Хорошо. Напомню, когда показы остановятся.", { decided: "kept" });

  // Разбор фраз в закрытый набор действий: заглушка на месте языковой модели. Менять можно только после подтверждения.
  override send(raw: string): void {
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
    if (/какой.*бюджет|сколько.*бюджет|каков.*бюджет/.test(t)) {
      const b = budgetOf(s);
      const spent = spentOf(s);
      return this.reply(text, `Бюджет ${rub(b)} в месяц, потрачено ${rub(spent)}. Изменить бюджет можно на вкладке «Настройки».`, {}, { label: "Открыть настройки", view: "settings" });
    }
    if (/бюджет|трат/.test(t) && num >= 1000) {
      const refusal = refusalOf(s, { action: "setting", value: ["budget", num] });
      if (refusal) return this.reply(text, refusal, { pending: null });
    }
    if (/бюджет|трат/.test(t) && num >= 1000) return this.reply(text, `Понял так: бюджет ${rub(num)} в месяц. Сначала обновлю лимит в Яндексе, потом перераспределю деньги. Применить?`, { pending: { action: "setting", value: ["budget", num] } });
    if (/клиент/.test(t) && num >= 0 && num <= week(s).leads) { this.set({ input: "" }); return this.run({ action: "clients", value: num }, text); }
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
}
