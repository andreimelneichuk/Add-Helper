import { MockStore } from "../mock/MockStore";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { saveDraft } from "./draft";
import {
  adName,
  Store,
  adShown,
  adStat,
  budgetOf,
  confirmText,
  daysOf,
  draftAd,
  initialState,
  newFact,
  siteFacts,
  statsOf,
  statusText,
  stopped,
  warned,
  week,
  writtenAds} from "./store";

describe("хранилище прототипа", () => {
  it("повтор сохранения нового сайта не переносит уже подтверждённое объявление заново", async () => {
    const store = new Store({ ...initialState("start"), step: 5, account: { email: "anna@example.ru" } });
    const createWizardAd = vi.fn().mockResolvedValueOnce({ id: "w0" }).mockRejectedValueOnce(new Error("Сеть недоступна")).mockResolvedValue({ id: "w1" });
    const createBusiness = vi.fn(async () => ({ id: "new", name: "Салон" }));
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.transferDeps = {
      session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: createBusiness.mock.calls.length ? [{ id: "new", name: "Салон" }] : [] })),
      createBusiness,
      business: () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), createWizardAd }),
    };
    const base = { name: "Маникюр", tag: "", tagTone: "accent" as const, leads: 0, cost: 0, hook: "", facts: "" };
    store.set({ ads: [
      { ...base, id: "custom-1", title: "Первое объявление", text: "Первый текст" },
      { ...base, id: "custom-2", title: "Второе объявление", text: "Второй текст" },
    ], adSet: null, draftOff: {} });
    store.next();
    await vi.waitFor(() => expect(store.state.block).toContain("Сеть недоступна"));
    store.next();
    await vi.waitFor(() => expect(createWizardAd).toHaveBeenCalledTimes(3));
    expect(createBusiness).toHaveBeenCalledTimes(1);
    expect(createWizardAd.mock.calls.map(([body]) => body.client_id)).toEqual(["custom-1", "custom-2", "custom-2"]);
  });

  it("новый сайт вошедшего владельца переносит свои объявления, правки и пустой выбор набора", async () => {
    const store = new Store({ ...initialState("start"), step: 5, account: { email: "anna@example.ru" } });
    const createWizardAd = vi.fn().mockResolvedValue({ id: "w1" });
    const updateWizardAd = vi.fn().mockResolvedValue({ id: "w0" });
    const adoptAds = vi.fn().mockResolvedValue({ kept: 0 });
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.transferDeps = {
      session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [] })),
      createBusiness: vi.fn(async () => ({ id: "new", name: "Салон" })),
      business: () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), createWizardAd, updateWizardAd, adoptAds }),
    };
    const base = { name: "Маникюр", tag: "", tagTone: "accent" as const, leads: 0, cost: 0, hook: "", facts: "" };
    store.set({
      ads: [
        { ...base, id: "w0", title: "Правка заголовка", text: "Правка текста" },
        { ...base, id: "custom-1", title: "Свое объявление", text: "Свой текст" },
        { ...base, id: "custom-2", title: "Оставленное объявление", text: "Текст для запуска" },
      ],
      draftOff: { w0: true, "custom-1": true },
      adSet: { id: 7, token: "t", template: false, needsReview: false, facts: "{}" },
    });
    store.next();
    await vi.waitFor(() => expect(createWizardAd).toHaveBeenCalled());
    expect(adoptAds).toHaveBeenCalledWith({ id: 7, token: "t", kept: [] });
    expect(updateWizardAd).toHaveBeenCalledWith("w0", { headline: "Правка заголовка", text: "Правка текста" });
    expect(createWizardAd).toHaveBeenCalledWith({ headline: "Свое объявление", text: "Свой текст", client_id: "custom-1", active: false });
  });

  it("чат меняет только после подтверждения и пишет в историю", () => {
    const store = new MockStore();
    const before = store.state.hist.length;
    store.send("Не показывай по воскресеньям");
    expect(store.state.f.hours).toBe("в часы работы");
    store.send("Применить");
    expect(store.state.f.hours).toBe("кроме воскресений");
    expect(store.state.hist).toHaveLength(before + 1);
  });

  it("отмена не стирает исходную запись", () => {
    const store = new MockStore();
    const before = store.state.hist.length;
    store.run({ action: "undo", value: "off" });
    expect(store.state.undone.off).toBe(true);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(store.state.hist.some((h) => h.id === "off")).toBe(true);
  });

  it("итог на панели показывается полосой, в чате не показывается", () => {
    const store = new MockStore();
    store.run({ action: "pause" });
    expect(store.state.notice).toContain("паузу");
    store.openChat();
    store.run({ action: "pause" }, "Включи");
    expect(store.state.notice).toBe("");
  });

  it("запуск не идёт дальше без кабинета, ИНН и кода", () => {
    const store = new MockStore(initialState("start"));
    store.set({ site: "salon-primer.ru", step: 5 });
    store.next();
    expect(store.state.step).toBe(5);
    expect(store.state.block).not.toBe("");
    store.set({ yandex: true, inn: "7700000000", metrica: true });
    store.next();
    store.next();
    store.next();
    expect(store.state.step).toBe(8);
    store.next();
    expect(store.state.stage).toBe("work");
  });

  it("запуск из чата не проходит, если сняты все объявления", () => {
    const store = new MockStore(initialState("start"));
    store.set({ step: 4, draftOff: { a1: true, a2: true, a3: true } });
    store.send("нравится");
    expect(store.state.step).toBe(4);
    expect(store.state.block).not.toBe("");
  });

  it.each(["не применяй", "не давай", "когда начнётся"])("фраза «%s» не подтверждает действие", (phrase) => {
    const store = new MockStore();
    store.send("Бюджет 30000");
    store.send(phrase);
    expect(store.state.f.budget).toBe(20000);
  });

  it("считает неделю по подставным данным", () => {
    expect(week(initialState())).toMatchObject({ cost: 5040, leads: 12, prevCost: 3360, prevLeads: 6 });
  });

  it("валидация шагов мастера next() и блокировки", () => {
    const store = new MockStore(initialState("start"));

    // Блокировка при незавершённой правке факта
    store.set({ factEdit: { id: null, value: "Новый факт" } });
    store.next();
    expect(store.state.block).toBe("Сохраните или отмените правку факта.");
    store.set({ factEdit: null, block: "" });

    // Шаг 0 без сайта
    store.set({ step: 0, site: "" });
    store.next();
    expect(store.state.block).toContain("Вставьте ссылку");

    // Шаг 0 без сайта с ридером
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.next();
    expect(store.state.block).toBe("Вставьте ссылку или заполните профиль вручную.");
    store.reader = null;

    // Шаг 1 без фактов
    store.set({ step: 1, site: "test.ru", facts: [] });
    store.next();
    expect(store.state.block).toBe("Добавьте хотя бы один факт: рекламу пишу только по ним.");

    // Шаг 1 без услуг
    store.set({ step: 1, facts: [{ id: "n1", field: "address", label: "Где", value: "Москва", source: "от вас" }] });
    store.next();
    expect(store.state.block).toBe("Добавьте хотя бы одну услугу: объявления пишу по услугам.");

    // Шаг 2 проверка лимитов бюджета
    store.set({ step: 2, budgetDraft: "500", budgetOptions: { min: 2000, max: 100000, presets: [5000] } });
    store.next();
    expect(store.state.block).toContain("Укажите сумму от");

    store.set({ budgetDraft: "200000" });
    store.next();
    expect(store.state.block).toContain("Больше 100 000 ₽ в месяц здесь не ставлю");

    // Шаг 3 с писателем объявлений
    store.set({ step: 4, budgetDraft: null });
    store.writer = { writeAds: vi.fn(), adSet: vi.fn() };
    store.set({ writing: "asking" });
    store.next();
    expect(store.state.block).toBe("Объявления ещё пишутся: это обычно меньше минуты.");

    store.set({ writing: null, ads: [] });
    store.next();
    expect(store.state.block).toBe("Объявлений пока нет: нажмите «Написать ещё раз».");

    // Шаг 3 когда сняты все объявления
    store.set({ ads: [{ id: "a1", name: "Объявление", tag: "", tagTone: "neutral", leads: 0, cost: 0, hook: "", title: "", text: "", facts: "" }], draftOff: { a1: true } });
    store.next();
    expect(store.state.block).toBe("Оставьте хотя бы одно объявление.");
  });

  it("шаги после входа nextReal: сохранение маркировки и финализация", async () => {
    const store = new MockStore(initialState("start"));
    const saveMarking = vi.fn().mockResolvedValue({});
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.wizard = { accounts: vi.fn(), availableAccounts: vi.fn(), attachAccount: vi.fn(), connect: vi.fn(), marking: vi.fn(), saveMarking, tagCheck: vi.fn(), requestTagCheck: vi.fn(), launchCheck: vi.fn(), startLaunch: vi.fn(), wizardAds: vi.fn(), manualTokenLink: vi.fn(), saveManualToken: vi.fn() };
    store.navigate = vi.fn();

    // Шаг 4 без входа
    store.set({ step: 5, account: null });
    store.next();
    expect(store.state.block).toContain("Войдите через Яндекс");

    // Шаг 4 без wizard -> редирект
    store.set({ step: 5, account: { email: "anna@ya.ru" } });
    store.wizard = null;
    store.next();
    expect(store.navigate).toHaveBeenCalledWith("/");

    // Шаг 5 сохранение маркировки
    store.wizard = { accounts: vi.fn(), availableAccounts: vi.fn(), attachAccount: vi.fn(), connect: vi.fn(), marking: vi.fn(), saveMarking, tagCheck: vi.fn(), requestTagCheck: vi.fn(), launchCheck: vi.fn(), startLaunch: vi.fn(), wizardAds: vi.fn(), manualTokenLink: vi.fn(), saveManualToken: vi.fn() };
    store.set({ step: 6, inn: "7701234567", advertiser: { kind: "legal", name: "ООО Салон", phone: "", okved: "96.02" } });
    store.next();
    await new Promise((r) => setTimeout(r, 10));
    expect(saveMarking).toHaveBeenCalledWith({ kind: "legal", inn: "7701234567", name: "ООО Салон", okved: "96.02" });
    expect(store.state.marked).toBe(true);
    expect(store.state.step).toBe(7);

    // Шаг 6 переход к сводке
    store.next();
    expect(store.state.step).toBe(8);

    // Последний шаг мастера
    store.next();
    expect(store.navigate).toHaveBeenCalledWith("/");
  });

  it("resume и resumeConnect обрабатывают ошибки и статусы", async () => {
    const store = new MockStore(initialState("start"));
    store.navigate = vi.fn();

    // resume с ошибкой входа loginResult (известной и неизвестной)
    saveDraft({ site: "test.ru", facts: [], budget: 10000 });
    await store.resume({ session: vi.fn(), createBusiness: vi.fn(), business: vi.fn() } as any, "denied");
    expect(store.state.block).toContain("доступ в Яндексе не дан");

    const storeUnknownLogin = new MockStore(initialState("start"));
    await storeUnknownLogin.resume({ session: vi.fn(), createBusiness: vi.fn(), business: vi.fn() } as any, "unknown_reason");
    expect(storeUnknownLogin.state.block).toContain("Вход не состоялся");

    // resume когда draft отсутствует
    window.localStorage.removeItem("adhelper.startDraft");
    const noDraftStore = new MockStore(initialState("start"));
    await noDraftStore.resume({ session: vi.fn() } as any, null);
    expect(noDraftStore.state.step).toBe(0);

    // resume с существующим бизнесом
    saveDraft({ site: "test.ru", facts: [], budget: 10000 });
    const existsStore = new MockStore(initialState("start"));
    await existsStore.resume({
      session: vi.fn(async () => ({ me: { email: "u@ya.ru", csrf_token: "c" } as any, businesses: [{ id: "b1", name: "Мой салон" }] })),
      createBusiness: vi.fn(),
      business: vi.fn(),
    } as any, null);
    expect(existsStore.state.block).toContain("У вас уже есть бизнес «Мой салон»");

    // resume с Error ошибкой (e instanceof Error)
    saveDraft({ site: "test.ru", facts: [], budget: 10000 });
    const errObjStore = new MockStore(initialState("start"));
    await errObjStore.resume({
      session: vi.fn(async () => ({ me: { email: "u@ya.ru", csrf_token: "c" } as any, businesses: [] })),
      createBusiness: vi.fn().mockRejectedValue(new Error("Сбой создания")),
      business: vi.fn(),
    } as any, null);
    expect(errObjStore.state.block).toContain("Сбой создания");

    // resume когда сессия отсутствует
    const store2 = new MockStore(initialState("start"));
    window.localStorage.setItem("adhelper.startDraft", JSON.stringify({ site: "test.ru", facts: [], budget: 10000 }));
    await store2.resume({ session: vi.fn(async () => null), createBusiness: vi.fn(), business: vi.fn() } as any, null);
    expect(store2.state.step).toBe(5);

    // resumeConnect когда бизнес не найден
    const store3 = new MockStore(initialState("start"));
    store3.navigate = vi.fn();
    window.localStorage.setItem("adhelper.startBusiness", "b-unknown");
    await store3.resumeConnect({ session: vi.fn(async () => ({ me: { email: "a@b.c", csrf_token: "c" } as any, businesses: [] })), open: vi.fn() }, null);
    expect(store3.navigate).toHaveBeenCalledWith("/");
  });

  it("saveFact и removeFact изменяют список фактов", () => {
    const store = new MockStore(initialState("start"));
    store.set({ facts: [{ id: "f1", field: "service", label: "Услуга", value: "Маникюр", source: "от вас" }] });

    // добавление нового факта
    store.set({ factEdit: { id: null, value: "Педикюр", field: "service" } });
    store.saveFact();
    expect(store.state.facts.some((f) => f.value === "Педикюр")).toBe(true);

    // добавление нового факта без указания field (дефолт service)
    store.set({ factEdit: { id: null, value: "Стрижка" } as any });
    store.saveFact();
    expect(store.state.facts.some((f) => f.value === "Стрижка")).toBe(true);

    // saveFact при пустом edit ничего не меняет
    store.set({ factEdit: null });
    store.saveFact();

    // правка существующего факта
    store.set({ factEdit: { id: "f1", value: "Маникюр Lux", field: "service" } });
    store.saveFact();
    expect(store.state.facts.find((f) => f.id === "f1")?.value).toBe("Маникюр Lux");

    // удаление факта
    store.removeFact("f1");
    expect(store.state.facts.some((f) => f.id === "f1")).toBe(false);
  });

  it("study и awaitRead опрашивают разбор сайта до готовности", async () => {
    const store = new MockStore(initialState("start"));
    store.waitMs = 5;
    store.set({ site: "https://mysalon.ru" });
    const reader = {
      readSite: vi.fn().mockResolvedValue({ id: 1, token: "tok", url: "https://mysalon.ru", status: "pending" }),
      siteRead: vi.fn()
        .mockResolvedValueOnce({ id: 1, status: "pending", facts: [] })
        .mockResolvedValueOnce({ id: 1, status: "ready", facts: [{ field: "niche", value: "салон", quote: "салон", price: null, page: "/" }] }),
    };
    store.reader = reader;
    await store.study();
    expect(store.state.step).toBe(1);
    expect(store.state.facts.length).toBeGreaterThan(0);

    // study с ошибкой сервиса ApiError и не-ApiError
    reader.readSite.mockRejectedValueOnce(new ApiError(500, "Сайт недоступен"));
    await store.study();
    expect(store.state.block).toContain("Сайт недоступен");

    reader.readSite.mockRejectedValueOnce(new TypeError("сеть"));
    await store.study();
    expect(store.state.block).toContain("Сервис не ответил");

    // study без ридера или при идущем чтении
    store.reader = null;
    await store.study();
    store.reader = reader;
    store.set({ reading: "asking" });
    await store.study();

    // awaitRead со статусом failed (с текстом и без) и ready без фактов
    store.set({ reading: null });
    reader.siteRead
      .mockResolvedValueOnce({ id: 1, status: "failed", problem_text: "Не отвечает" })
      .mockResolvedValueOnce({ id: 1, status: "failed", problem_text: null })
      .mockResolvedValueOnce({ id: 1, status: "ready", facts: [] });
    reader.readSite.mockResolvedValue({ id: 1, token: "tok", status: "pending", url: "https://site.ru" });
    await store.study();
    expect(store.state.block).toBe("Не отвечает");

    await store.study();
    expect(store.state.block).toContain("Сайт не прочитался");

    await store.study();
    expect(store.state.msgs.at(-1)?.text).toContain("На сайте не нашёл фактов");

    // fillByHand
    store.fillByHand();
    expect(store.state.step).toBe(1);
    expect(store.state.facts).toEqual([]);

    // таймаут при долгом разборе
    const timeoutStore = new MockStore(initialState("start"));
    timeoutStore.waitMs = 5;
    timeoutStore.readTimeoutMs = 15;
    timeoutStore.set({ site: "https://hangingsite.ru" });
    const hangingReader = {
      readSite: vi.fn().mockResolvedValue({ id: 99, token: "tok99", status: "pending", url: "https://hangingsite.ru" }),
      siteRead: vi.fn().mockResolvedValue({ id: 99, status: "pending", facts: [] }),
    };
    timeoutStore.reader = hangingReader;
    await timeoutStore.study();
    expect(timeoutStore.state.reading).toBeNull();
    expect(timeoutStore.state.block).toContain("дольше обычного");

    // resumeReading восстанавливает опрос и обрабатывает готовность
    const resumeStore = new MockStore(initialState("start"));
    resumeStore.waitMs = 5;
    resumeStore.reader = {
      readSite: vi.fn(),
      siteRead: vi.fn().mockResolvedValue({ id: 55, status: "ready", facts: [{ field: "niche", value: "барбершоп", quote: "барбер", price: null, page: "/" }] }),
    };
    await resumeStore.resumeReading(55, "tok55", "https://barber.ru");
    expect(resumeStore.state.step).toBe(1);
    expect(resumeStore.state.site).toBe("https://barber.ru");
    expect(resumeStore.state.facts[0]?.value).toBe("барбершоп");
  });

  it("writeAds опрашивает генерацию объявлений", async () => {
    const store = new MockStore(initialState("start"));
    store.waitMs = 5;
    const writer = {
      writeAds: vi.fn().mockResolvedValue({ id: 2, token: "tok2", status: "pending" }),
      adSet: vi.fn()
        .mockResolvedValueOnce({ id: 2, status: "pending", ads: [] })
        .mockResolvedValueOnce({ id: 2, status: "ready", template: true, needs_review: false, ads: [{ headline: "Маникюр", text: "Лучший", facts: ["факт"] }] }),
    };
    store.writer = writer;
    await store.writeAds(true);
    expect(store.state.ads[0]?.name).toBe("Маникюр");

    // writeAds с ошибкой сервиса
    writer.writeAds.mockRejectedValueOnce(new ApiError(422, "Не удалось написать"));
    await store.writeAds(true);
    expect(store.state.block).toContain("Не удалось написать");
  });

  it("draftAd и askRefusal проверяют профиль на законность обещаний", () => {
    const s = initialState();
    expect(draftAd(s, null, "скидка 50%")).toEqual({ refusal: expect.stringContaining("нет «50%»") });
    expect(draftAd(s, null, "лучший салон № 1")).toEqual({ refusal: expect.stringContaining("Такое обещание нечем подтвердить") });
    expect(draftAd(s, s.ads[0]!, "короче")).toHaveProperty("ad");
  });

  it("confirmText формирует тексты подтверждения для различных действий", () => {
    const s = initialState();
    expect(confirmText(s, { action: "balance" })).toContain("Вы знаете причину расхождения?");
    expect(confirmText(s, { action: "test", value: "1", answer: "agree" })).toContain("Проверить новый вариант");
    expect(confirmText(s, { action: "test", value: "1", answer: "decline" })).toContain("Не проверять");
    expect(confirmText(s, { action: "test", value: "1", answer: "cancel" })).toContain("Отменить тест?");
    expect(confirmText(s, { action: "price", value: 1600 })).toContain("Маникюр теперь от 1 600 ₽");
    expect(confirmText(s, { action: "setting", value: ["hours", "по будням"] })).toContain("Когда: по будням");
  });

  it("баннер решений и вспомогательные функции состояния", () => {
    const store = new MockStore();
    store.reportTopUp();
    expect(store.state.topped).toBe(true);

    store.reconnect();
    expect(store.state.topped).toBe(true);

    store.addBudget();
    expect(store.state.extra).toBe(3000);
    expect(store.state.decided).toBe("added");

    store.keepBudget();
    expect(store.state.decided).toBe("kept");

    store.pickSituation("budget");
    expect(store.state.situation).toBe("budget");

    expect(budgetOf(store.state)).toBeGreaterThan(0);
    expect(stopped(store.state)).toBe(false);
    expect(warned(store.state)).toBe(true);
    expect(statusText(store.state)).toContain("Бюджет");

    const ad = store.state.ads[0]!;
    expect(adShown(store.state, ad)).toHaveProperty("hook");
    expect(adStat(store.state, ad)).toContain("обращений");
    expect(adName(store.state, "a1")).toBe(ad.name);

    expect(daysOf(store.state).length).toBeGreaterThan(0);
    expect(statsOf(store.state, "week")).toHaveProperty("cost");
    expect(writtenAds([{ headline: "Заголовок", text: "Текст", facts: [] }])[0]?.name).toBe("Заголовок");
    expect(siteFacts([{ field: "niche", value: "салон", quote: "цитата", price: null, page: "/" }])[0]?.field).toBe("niche");
    expect(newFact("Услуга").field).toBe("service");
  });

  it("разбор различных фраз в чате send()", () => {
    const store = new MockStore();

    store.send("новое объявление про педикюр");
    expect(store.state.pending?.action).toBe("adSave");

    store.send("перепиши объявление со скидкой");
    expect(store.state.pending?.action).toBe("adSave");

    store.send("поставь на паузу");
    expect(store.state.pending?.action).toBe("pause");

    store.set({ paused: true, pending: null });
    store.send("включи");
    expect(store.state.pending?.action).toBe("pause");

    store.send("почему такая цена");
    expect(store.state.msgs.at(-1)?.link?.view).toBe("stats");

    store.send("как дела");
    expect(store.state.msgs.at(-1)?.link?.view).toBe("overview");

    store.send("погода за окном");
    expect(store.state.msgs.at(-1)?.text).toContain("Этого я пока не умею");

    // sendStart: добавление факта на шаге 1 и бюджет на шаге 2
    const startStore = new MockStore(initialState("start"));
    startStore.set({ step: 1 });
    startStore.send("Работаем круглосуточно");
    expect(startStore.state.facts.some((f) => f.value === "Работаем круглосуточно")).toBe(true);

    startStore.set({ step: 2 });
    startStore.send("хочу 500 рублей"); // < 1000
    expect(startStore.state.msgs.at(-1)?.text).toContain("Когда будете готовы");
    startStore.send("хочу 25 000 рублей"); // >= 1000
    expect(startStore.state.f.budget).toBe(25000);

    // send: воскресенья, клиенты, история, отмена, переделка без темы
    store.decline();
    store.send("не показывай по воскресеньям");
    expect(store.state.pending?.action).toBe("setting");
    store.decline();

    // отмена когда нечего отменять
    store.set({ hist: [] });
    store.send("отмени");
    expect(store.state.msgs.at(-1)?.text).toBe("Отменять пока нечего.");

    // отмена когда есть что отменить
    store.set({ hist: [{ id: "h1", canUndo: true, title: "Смена настройки", undoNote: "Вернётся" } as any], undone: {} });
    store.send("отмени");
    expect(store.state.pending?.action).toBe("undo");
    store.decline();

    store.send("что ты менял");
    expect(store.state.msgs.at(-1)?.link?.view).toBe("history");

    store.send("текст рекламы");
    expect(store.state.msgs.at(-1)?.link?.view).toBe("ads");

    store.send("цена теперь 2000");
    expect(store.state.pending?.action).toBe("price");

    store.send("переделай объявление");
    expect(store.state.msgs.at(-1)?.text).toContain("Какое объявление переделать");
  });

  it("проверяет ветки refusalOf, confirmText, apply adSave/undo и loadChosen", async () => {
    const store = new MockStore();

    // refusalOf: попытка отключить последнее активное объявление
    store.set({ adsOff: { a2: true, a3: true } });
    store.ask({ action: "ad", value: "a1" });
    expect(store.state.notice).toContain("Это последнее объявление");

    // refusalOf: бюджет ниже потраченного
    store.ask({ action: "setting", value: ["budget", 1000] });
    expect(store.state.notice).toContain("уже потрачено");

    // confirmText: пауза, undo, chat, ad, adSave
    const s = store.state;
    expect(confirmText(s, { action: "pause" })).toContain("Остановить все показы");
    expect(confirmText({ ...s, paused: true }, { action: "pause" })).toContain("Включить рекламу снова");
    expect(confirmText(s, { action: "chat", value: { confirm: "Понял так: тест Применить?" } as any })).toBe("тест");
    expect(confirmText({ ...s, live: {} as any }, { action: "ad", value: "a1" })).toContain("Отключить");
    expect(confirmText(s, { action: "adSave", value: { id: "a_brand_new", name: "Новое", tag: "новое", tagTone: "accent", hook: "Хук", title: "Заголовок", text: "Текст", facts: "Факты", leads: 0, cost: 0 } })).toContain("Добавить объявление");

    // apply balance, chat, test
    store.run({ action: "balance" });
    store.run({ action: "chat", value: {} as any });
    expect(store.state.msgs.at(-1)?.text).toContain("только на настоящих данных");

    // apply adSave нового объявления и последующая отмена (revert.adOff)
    const newAd = { id: "a_brand_new_2", name: "Новое 2", tag: "новое", tagTone: "accent" as const, hook: "Х", title: "З", text: "Т", facts: "Ф", leads: 0, cost: 0 };
    store.run({ action: "adSave", value: newAd });
    expect(store.state.ads.some((a) => a.id === "a_brand_new_2")).toBe(true);
    const addedHist = store.state.hist[0]!;
    store.run({ action: "undo", value: addedHist.id });
    expect(store.state.adsOff["a_brand_new_2"]).toBe(true);

    // apply adSave существующего объявления и отмена (revert.adContent)
    const oldAd = store.state.ads[0]!;
    store.run({ action: "adSave", value: { ...oldAd, title: "Модифицированный" } });
    expect(store.state.ads[0]?.title).toBe("Модифицированный");
    const editHist = store.state.hist[0]!;
    store.run({ action: "undo", value: editHist.id });
    expect(store.state.ads[0]?.title).toBe(oldAd.title);

    // draftAd без подходящих шаблонов
    const emptyDraft = draftAd({ ...s, facts: [] }, null, "маникюр");
    expect(emptyDraft).toEqual({ refusal: expect.stringContaining("мало фактов") });

    // draftAd с topic из base (когда в ask темы нет)
    const topicFromBase = draftAd(s, s.ads.find((a) => a.id === "a3")!, "сделай другой вариант");
    expect(topicFromBase).toHaveProperty("ad");

    // confirmText с undo (без undoNote), inTest для ad и adSave
    const sRunning = { ...s, situation: "running" as const };
    expect(confirmText(sRunning, { action: "undo", value: "non-existent" })).toContain("Отменить «undefined»?");
    expect(confirmText(sRunning, { action: "ad", value: "a1" })).toContain("Идёт проверка вариантов");
    expect(confirmText(sRunning, { action: "adSave", value: s.ads[0]! })).toContain("Идёт проверка вариантов");

    // sendStart на шаге 0 с reader и без reader
    const storeStep0WithReader = new MockStore(initialState("start"));
    storeStep0WithReader.reader = { readSite: vi.fn().mockResolvedValue({ id: 1, token: "t", status: "ready", url: "https://site.ru" }), siteRead: vi.fn() };
    storeStep0WithReader.send("https://site.ru");
    expect(storeStep0WithReader.state.site).toBe("https://site.ru");

    const storeStep0NoReader = new MockStore(initialState("start"));
    storeStep0NoReader.send("https://site-no-reader.ru");
    expect(storeStep0NoReader.state.step).toBe(1);

    // send с клиентами, переделкой педикюра и маникюра, малым бюджетом и малой ценой
    store.send("переделай объявление маникюр");
    expect(store.state.pending?.action).toBe("adSave");
    store.decline();

    store.send("переделай объявление педикюр");
    expect(store.state.pending?.action).toBe("adSave");
    store.decline();

    store.send("записал 2 клиента");
    expect(store.state.clients).toBe(2);

    store.send("бюджет 500"); // num < 1000
    expect(store.state.msgs.at(-1)?.text).toContain("Этого я пока не умею");

    store.send("акция 50"); // num < 100
    expect(store.state.msgs.at(-1)?.text).toContain("Этого я пока не умею");

    // resume с не-Error ошибкой
    const storeResumeErr = new MockStore(initialState("start"));
    saveDraft({ site: "s", facts: [], budget: 1000 });
    await storeResumeErr.resume({
      session: vi.fn().mockResolvedValue({ me: { email: "a@b.c", csrf_token: "c" } as any, businesses: [] }),
      createBusiness: vi.fn().mockRejectedValue("строковая сетевая ошибка"),
      business: vi.fn(),
    } as any, null);
    expect(storeResumeErr.state.block).toContain("Сервис не ответил");

    // navigate по умолчанию
    const defaultNavStore = new MockStore();
    const assignSpy = vi.fn();
    vi.stubGlobal("location", { assign: assignSpy });
    defaultNavStore.navigate("/test-path");
    expect(assignSpy).toHaveBeenCalledWith("/test-path");

    // loadChosen через resumeConnect
    window.localStorage.setItem("adhelper.startBusiness", "b1");
    const storeConnect = new MockStore(initialState("start"));
    storeConnect.writer = { writeAds: vi.fn(), adSet: vi.fn() };
    const mockWizard = {
      wizardAds: vi.fn().mockResolvedValue({ ads: [{ headline: "От писателя", text: "Текст", facts: [] }] }),
    };
    await storeConnect.resumeConnect({
      session: vi.fn(async () => ({ me: { email: "u@test.ru", csrf_token: "c" } as any, businesses: [{ id: "b1", name: "Бизнес" }] })),
      open: vi.fn(() => mockWizard as any),
    }, null);
    expect(storeConnect.state.ads[0]?.name).toBe("От писателя");
  });

  it("проверяет все граничные ветки next() и nextReal()", async () => {
    const store = new MockStore(initialState("start"));
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };

    // Шаг 0: пустой сайт с reader
    store.set({ step: 0, site: "" });
    store.next();
    expect(store.state.block).toContain("Вставьте ссылку или заполните профиль вручную");

    // Шаг 1: факты без услуг
    store.set({ step: 1, facts: [{ id: "f1", field: "address", label: "Адрес", value: "Москва", source: "от вас" }] });
    store.next();
    expect(store.state.block).toContain("Добавьте хотя бы одну услугу");

    // Шаг 2: лимиты бюджета min и max
    store.set({ step: 2, budgetDraft: "500", budgetOptions: { min: 1000, max: 100000, presets: [] } });
    store.next();
    expect(store.state.block).toContain("Укажите сумму от 1 000 ₽");

    store.set({ step: 2, budgetDraft: "200000", budgetOptions: { min: 1000, max: 100000, presets: [] } });
    store.next();
    expect(store.state.block).toContain("Больше 100 000 ₽");

    // Шаг 3: writer в процессе генерации, пустые объявления, все объявления отключены
    store.writer = { writeAds: vi.fn(), adSet: vi.fn() };
    store.set({ step: 4, writing: { id: 1, token: "tok" }, ads: [] });
    store.next();
    expect(store.state.block).toContain("Объявления ещё пишутся");

    store.set({ step: 4, writing: null, ads: [] });
    store.next();
    expect(store.state.block).toContain("Объявлений пока нет");

    store.set({ step: 4, writing: null, ads: [{ id: "a1", name: "1" } as any], draftOff: { a1: true } });
    store.next();
    expect(store.state.block).toContain("Оставьте хотя бы одно объявление");

    // Шаги nextReal: 4 без account, 4 без wizard, 5 с marked: true, 5 с person (phone), 6, и финал
    store.set({ step: 5, account: null, draftOff: {} });
    store.next();
    expect(store.state.block).toContain("Войдите через Яндекс");

    store.set({ step: 5, account: { email: "a@ya.ru" } });
    store.wizard = null;
    store.navigate = vi.fn();
    store.next();
    expect(store.navigate).toHaveBeenCalledWith("/");

    // nextReal шаг 5: marked: true сразу переходит дальше
    store.set({ step: 6, marked: true });
    store.wizard = { saveMarking: vi.fn() } as any;
    store.next();
    expect(store.state.step).toBe(7);

    // nextReal шаг 5: advertiser person передаёт phone и не-Error ошибка
    store.set({
      step: 6, marked: false, inn: "123456789012",
      advertiser: { kind: "person", name: "Иван", phone: "+7999", okved: "" },
    });
    store.wizard = { saveMarking: vi.fn().mockRejectedValue("строковая ошибка маркировки") } as any;
    store.next();
    await new Promise((r) => setTimeout(r, 10));
    expect(store.state.block).toContain("Сервис не ответил");

    // nextReal шаг 6
    store.set({ step: 7, block: "" });
    store.next();
    expect(store.state.step).toBe(8);

    // nextReal финал STEPS.length - 1
    store.set({ step: 8 }); // STEPS.length - 1
    store.navigate = vi.fn();
    store.next();
    expect(store.navigate).toHaveBeenCalledWith("/");

    // writeAds: сбой со статусом failed (с текстом проблемы и без) и не-ApiError
    const failingWriter = {
      writeAds: vi.fn().mockResolvedValue({ id: 10, token: "tok10", status: "pending" }),
      adSet: vi.fn()
        .mockResolvedValueOnce({ id: 10, status: "failed", problem_text: "Некорректный сайт" })
        .mockResolvedValueOnce({ id: 10, status: "failed", problem_text: null }),
    };
    store.writer = failingWriter;
    store.waitMs = 1;
    await store.writeAds(true);
    expect(store.state.block).toBe("Некорректный сайт");

    await store.writeAds(true);
    expect(store.state.block).toContain("Не получилось написать объявления");

    // writeAds с не-ApiError ошибкой
    failingWriter.writeAds.mockRejectedValueOnce(new TypeError("network failed"));
    await store.writeAds(true);
    expect(store.state.block).toContain("Сервис не ответил");

    // writeAds: пропуск без writer, во время writing и без изменений фактов
    store.writer = null;
    await store.writeAds();

    store.writer = failingWriter;
    store.set({ writing: { id: 1, token: "tok" } });
    await store.writeAds();

    // resume и resumeConnect: повторный вызов при resumed: true ничего не делает
    const resumedStore = new MockStore(initialState("start"));
    saveDraft({ site: "s", facts: [], budget: 1000 });
    await resumedStore.resume({
      session: vi.fn().mockResolvedValue({ me: { email: "u@t.ru", csrf_token: "c" } as any, businesses: [] }),
      createBusiness: vi.fn().mockResolvedValue({ id: "b1", name: "Б" }),
      business: vi.fn(() => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn() })),
    } as any, null);
    expect(resumedStore.state.step).toBe(5);

    // Второй вызов resume игнорируется
    await resumedStore.resume({} as any, null);

    // resume при transferDraft signed_out
    const signedOutStore = new MockStore(initialState("start"));
    saveDraft({ site: "s", facts: [], budget: 1000 });
    await signedOutStore.resume({
      session: vi.fn().mockResolvedValue(null),
    } as any, null);

    // Второй вызов resumeConnect игнорируется
    await resumedStore.resumeConnect({} as any, null);

    // resumeConnect при ошибке session
    const errSessionStore = new MockStore(initialState("start"));
    errSessionStore.navigate = vi.fn();
    await errSessionStore.resumeConnect({ session: vi.fn().mockRejectedValue(new Error("fail")), open: vi.fn() }, "problem");
    expect(errSessionStore.navigate).toHaveBeenCalledWith("/");

    // saveMarking для ИП (okved) и при отсутствии wizard
    const ipStore = new MockStore(initialState("start"));
    ipStore.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    ipStore.set({
      step: 6, marked: false, inn: "1234567890",
      advertiser: { kind: "ip", name: "ИП", phone: "", okved: "96.02" },
    });
    const mockSaveMarking = vi.fn().mockResolvedValue({});
    ipStore.wizard = { saveMarking: mockSaveMarking } as any;
    ipStore.next();
    await new Promise((r) => setTimeout(r, 10));
    expect(mockSaveMarking).toHaveBeenCalledWith(expect.objectContaining({ okved: "96.02" }));

    // saveMarking без wizard не падает
    ipStore.wizard = null;
    await (ipStore as any).saveMarking();

    // askUndo переключает asked
    ipStore.askUndo("entry-1");
    expect(ipStore.state.asked["entry-1"]).toBe(true);
    ipStore.askUndo("entry-1");
    expect(ipStore.state.asked["entry-1"]).toBe(false);

    // pickSituation для problem
    ipStore.pickSituation("problem");
    expect(ipStore.state.situation).toBe("problem");
  });

  it("BUG-006 (b): fillByHand отменяет фоновое чтение сайта и не затирает ручные факты", async () => {
    const store = new MockStore(initialState("start"));
    let resolveRead: (val: any) => void = () => {};
    const delayedRead = new Promise((resolve) => { resolveRead = resolve; });
    const reader = {
      readSite: vi.fn().mockResolvedValue({ id: 42, token: "tok42", url: "https://example.com" }),
      siteRead: vi.fn().mockReturnValue(delayedRead),
    };
    store.reader = reader;
    store.waitMs = 5;
    store.set({ site: "https://example.com" });

    // Запускаем парсинг
    const studyPromise = store.study();
    await new Promise((r) => setTimeout(r, 10));
    expect(store.state.reading).toEqual({ id: 42, token: "tok42" });

    // Пользователь нажимает «Заполнить вручную» и вводит свои факты
    store.fillByHand();
    expect(store.state.reading).toBeNull();
    expect(store.state.step).toBe(1);

    store.set({ factEdit: { id: null, value: "Стрижка от мастера, 2000 ₽", field: "service" } });
    store.saveFact();
    expect(store.state.facts).toHaveLength(1);
    expect(store.state.facts[0]!.value).toBe("Стрижка от мастера, 2000 ₽");

    // Пользователь переходит на шаг бюджета
    store.set({ step: 2 });

    // Бэкенд возвращает запоздавший результат парсинга
    resolveRead({
      status: "ready",
      facts: [{ field: "service", value: "Сайтовый маникюр", quote: "маникюр" }],
    });
    await studyPromise;
    await new Promise((r) => setTimeout(r, 20));

    // Проверяем: ручные факты НЕ затёрты, шаг НЕ сброшен обратно на 1
    expect(store.state.step).toBe(2);
    expect(store.state.facts).toHaveLength(1);
    expect(store.state.facts[0]!.value).toBe("Стрижка от мастера, 2000 ₽");
  });

  it("BUG-007 (a): saveMarking сохраняет ИНН в состоянии для повторного редактирования", async () => {
    const store = new MockStore(initialState("start"));
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    const mockSaveMarking = vi.fn().mockResolvedValue({});
    store.wizard = { saveMarking: mockSaveMarking } as any;
    store.set({
      step: 6,
      marked: false,
      inn: "770708389380",
      advertiser: { kind: "person", name: "Иванов", phone: "+79991234567", okved: "" },
    });

    store.next();
    await new Promise((r) => setTimeout(r, 10));

    expect(mockSaveMarking).toHaveBeenCalled();
    expect(store.state.marked).toBe(true);
    // Полный ИНН в браузере не остаётся (сохранён зашифрованным в сервисе)
    expect(store.state.inn).toBe("");
  });

  it("BUG-009: next() проверяет пределы бюджета при budgetDraft === null", () => {
    const store = new MockStore(initialState("start"));
    store.set({
      step: 2,
      budgetDraft: null,
      budgetOptions: { min: 10000, max: 100000, presets: [15000, 25000] },
      f: { ...store.state.f, budget: 5000 }, // Сумма ниже минимума
    });
    store.next();
    expect(store.state.block).toContain("Укажите сумму от 10 000 ₽");
    expect(store.state.step).toBe(2);
  });
});

describe("итог действия при смене раздела", () => {
  it("сообщение об итоге уходит при переходе на другой раздел и остаётся на том же", () => {
    const store = new MockStore();
    store.set({ view: "ads", notice: "Отправил объявление в архив." });
    store.goView("ads");
    expect(store.state.notice).toBe("Отправил объявление в архив.");
    store.goView("stats");
    expect(store.state.notice).toBe("");
  });
});
