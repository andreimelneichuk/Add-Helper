import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { Api, Profile } from "../api";
import { fakeApi, line, open, overview, said, settled } from "./testLive";
import { rub } from "./data";
import { statusText, week} from "./store";
import { MockStore } from "../mock/MockStore";
import { liveApi, savedText } from "./live";

describe("живые данные", () => {
  it("показывает сохранённый ручной черновик даже без заполненного профиля", async () => {
    const wizardAds = vi.fn().mockResolvedValue({ ads: [{ id: "w1", headline: "Мой текст", text: "Запишитесь", facts: [] }] });
    const profile = vi.fn().mockResolvedValue({ niche: "", services: [], features: [], website: "", address: "" });
    const store = await open(fakeApi({ wizardAds, profile }));
    expect(store.state.ads.map((a) => a.id)).toEqual(["anna/11", "w1"]);
  });

  it("показывает черновики и сохраняет статус ожидающей или отправленной публикации", async () => {
    const wizardAds = vi.fn().mockResolvedValue({ ads: [
      { id: "w0", headline: "Другая услуга", text: "Текст", facts: [], submitted: true, publication_status: "submitted" },
      { id: "w1", headline: "Новое объявление", text: "Запишитесь", facts: [], active: true, submitted: false },
      { id: "w2", headline: "Выключенный черновик", text: "Запишитесь", facts: [], active: false },
      { id: "w3", headline: "Архивный черновик", text: "Запишитесь", facts: [], archived: true },
      { id: "w4", headline: "В очереди", text: "Подождите", facts: [], publication_status: "pending" },
    ] });
    const store = await open(fakeApi({ wizardAds }));
    expect(wizardAds).toHaveBeenCalledWith(true);
    expect(store.state.ads.map((a) => a.id)).toEqual(["anna/11", "w0", "w1", "w2", "w3", "w4"]);
    expect(store.state.ads.find((a) => a.id === "w0")?.tag).toBe("на проверке Яндекса");
    expect(store.state.ads.find((a) => a.id === "w1")?.tag).toBe("готово к запуску");
    expect(store.state.ads.find((a) => a.id === "w2")?.tag).toBe("отключено");
    expect(store.state.ads.find((a) => a.id === "w3")?.tag).toBe("в архиве");
    expect(store.state.ads.find((a) => a.id === "w4")?.tag).toBe("отправляется");
  });

  it("переводит микроединицы в рубли только для показа и заполняет дни без статистики", async () => {
    const store = await open(fakeApi());
    const { live } = store.state;
    expect(live?.budget).toBe(20000);
    expect(live?.spent).toBe(1500);
    expect(live?.monthDays).toBe(30);
    expect(live?.days).toHaveLength(15);
    expect(live?.days[0]).toEqual([0, 0]);
    expect(week(store.state)).toMatchObject({ cost: 700, leads: 2 });
    expect(store.state.ads[0]).toMatchObject({ id: "anna/11", leads: 2, cost: 700 });
    // Цены профиля хранятся в рублях, а не в микроединицах.
    expect(live?.profile).toContainEqual(["Услуги и цены", `Маникюр, ${rub(1200)}`]);
  });

  it("не подмешивает подставные объявления и историю", async () => {
    const store = await open(fakeApi());
    expect(store.state.ads.map((a) => a.name)).toEqual(["Маникюр"]);
    expect(store.state.hist.map((h) => h.id)).toEqual(["change-5"]);
    expect(store.state.hist[0]).toMatchObject({ kind: "ads", mine: true, canUndo: true, about: "anna/11" });
  });

  it("объявление с картинкой показывает её, без картинки — только текст", async () => {
    const api = fakeApi({ ads: vi.fn(async () => [line({ image_id: 7 }), line({ ad_id: "12", image_id: null })]) } as Partial<Api>);
    const store = await open(api);
    expect(store.state.ads.find((a) => a.id === "anna/11")?.photo).toBe("/api/businesses/b1/photos/7");
    expect(store.state.ads.find((a) => a.id === "anna/12")?.photo).toBeUndefined();
  });

  it("обновляет статусы Direct у уже открытых карточек", async () => {
    let current = [line({ state: "moderation" })];
    const api = fakeApi({ ads: vi.fn(async () => current) } as Partial<Api>);
    const store = await open(api);
    expect(store.state.ads[0]?.tag).toBe("на проверке Яндекса");

    current = [
      line({ state: "archived" }),
      line({ ad_id: "12", title: "Новое объявление", state: "moderation" }),
    ];
    await store.refreshAds();

    expect(store.state.ads.map(({ id, tag }) => [id, tag])).toEqual([
      ["anna/11", "заменено"],
      ["anna/12", "на проверке Яндекса"],
    ]);
  });

  it("не дублирует карточку мастера, когда объявление уже есть в Direct", async () => {
    const api = fakeApi({
      ads: vi.fn(async () => [line({ state: "archived" })]),
      wizardAds: vi.fn(async () => ({ ads: [
        { id: "w0", headline: "Маникюр", text: "Текст", facts: [], submitted: true, publication_status: "submitted" },
        { id: "w1", headline: "Новый черновик", text: "Приходите", facts: [], submitted: true, publication_status: "submitted" },
      ] })),
    } as Partial<Api>);
    const store = await open(api);

    expect(store.state.ads.map(({ id, tag }) => [id, tag])).toEqual([
      ["anna/11", "заменено"],
      ["w1", "на проверке Яндекса"],
    ]);
  });

  it("тревогу показывает в состоянии", async () => {
    const alert = { kind: "zero_balance", details: {}, created_at: "2026-11-15T00:00:00Z" };
    const store = await open(fakeApi({ overview: vi.fn(async () => overview([alert])) } as Partial<Api>));
    expect(statusText(store.state)).toBe("Показы остановлены");
  });

  it("берёт баланс аккаунта из overview, когда реклама ещё не запущена", async () => {
    const api = fakeApi({
      overview: vi.fn(async () => ({
        ...overview(),
        money: [],
        campaigns: [],
        account_balance_micros: 12_500 * 1_000_000,
        account_login: "anna",
      })),
    } as unknown as Partial<Api>);
    const store = await open(api);
    expect(store.state.live?.balance).toBe(12500);
    expect(store.state.live?.account).toBe("anna");
  });
});

describe("действия идут через сервис", () => {
  it("своё объявление после запуска остаётся видно после перечитывания кабинета", async () => {
    const created = { id: "w1", headline: "Новое объявление", text: "Запишитесь", facts: [] };
    let saved = false;
    const createWizardAd = vi.fn(async () => { saved = true; return created; });
    const wizardAds = vi.fn(async () => ({ ads: saved ? [created] : [] }));
    const store = await open(fakeApi({ createWizardAd, wizardAds }));
    const ad = { ...store.state.ads[0]!, id: "custom-new", title: created.headline, text: created.text };
    store.run({ action: "adSave", value: ad });
    await settled();
    expect(createWizardAd).toHaveBeenCalledWith({ headline: created.headline, text: created.text });
    await store.refresh();
    expect(store.state.ads.map((a) => a.id)).toEqual(["anna/11", "w1"]);
    expect(store.state.ads.find((a) => a.id === "w1")?.tag).toBe("готово к запуску");
  });

  it("при ожидающей остановке не сообщает об архивации и позволяет повторить её", async () => {
    const archiveAd = vi.fn().mockResolvedValueOnce({ status: "pending" }).mockResolvedValueOnce({ status: "applied" });
    const store = await open(fakeApi({ archiveAd }));
    store.set({ openAd: "anna/11" });
    store.run({ action: "adArchive", value: "anna/11" });
    await settled();
    expect(store.state.notice).toContain("повторите архивацию");
    expect(store.state.ads[0]?.tag).not.toBe("в архиве");
    expect(store.state.openAd).toBe("anna/11");

    store.run({ action: "adArchive", value: "anna/11" });
    await settled();
    expect(archiveAd).toHaveBeenCalledTimes(2);
    expect(store.state.notice).toContain("в архив");
    expect(store.state.openAd).toBeNull();
  });

  it("отключение объявления вызывает API и перечитывает данные", async () => {
    const api = fakeApi();
    const store = await open(api);
    store.ask({ action: "ad", value: "anna/11" });
    expect(store.state.notice).toContain("последнее");
    store.run({ action: "ad", value: "anna/11" });
    await settled();
    expect(api.setAdState).toHaveBeenCalledWith("anna", "11", false, expect.any(String));
    expect(store.state.notice).toContain("Отключил «Маникюр»");
  });

  it("блокирует повторное действие, пока сервис не ответил", async () => {
    let finish!: (value: { status: "applied" }) => void;
    const setAdState = vi.fn(() => new Promise<{ status: "applied" }>((resolve) => { finish = resolve; }));
    const store = await open(fakeApi({ setAdState } as Partial<Api>));
    const action = { action: "ad", value: "anna/11" } as const;
    store.run(action);
    expect(store.state.working).toEqual(action);
    store.run(action);
    expect(setAdState).toHaveBeenCalledTimes(1);
    finish({ status: "applied" });
    await settled();
    expect(store.state.working).toBeNull();
  });

  it("отказ сервиса показывает как есть и ничего не меняет на экране", async () => {
    const api = fakeApi({ setAdState: vi.fn(async () => { throw new ApiError(503, "Кабинет Яндекса пока не подключён, поэтому ничего не изменилось"); }) } as Partial<Api>);
    const store = await open(api);
    store.run({ action: "ad", value: "anna/11" });
    await settled();
    expect(store.state.notice).toContain("не подключён");
    expect(store.state.adsOff["anna/11"]).toBeFalsy();
  });

  it("отмена передаёт номер изменения", async () => {
    const api = fakeApi();
    const store = await open(api);
    store.run({ action: "undo", value: "change-5" });
    await settled();
    expect(api.rollback).toHaveBeenCalledWith(5, false);
    expect(store.state.undone["change-5"]).toBe(true);
  });

  it("перед отменой спрашивает сервис о последствиях", async () => {
    const api = fakeApi({ rollbackPreview: vi.fn(async () => ({ possible: true, status: "ready", text: "Вернутся прежние часы показа: круглосуточно. Для всех 2 направлений этой просьбы.", directions: 2 })) } as Partial<Api>);
    const store = await open(api);
    store.askUndo("change-5");
    await settled();
    expect(api.rollbackPreview).toHaveBeenCalledWith(5);
    expect(store.state.asked["change-5"]).toBe(true);
    expect(store.state.undoInfo["change-5"]?.directions).toBe(2);
    store.run({ action: "undo", value: "change-5", whole: true });
    await settled();
    expect(api.rollback).toHaveBeenCalledWith(5, true);
    expect(store.state.notice).toContain("для всех 2 направлений");
  });

  it("если отменить нельзя, показывает причину от сервиса", async () => {
    const api = fakeApi({ rollbackPreview: vi.fn(async () => { throw new ApiError(503, "Кабинет Яндекса пока не подключён, поэтому ничего не изменилось"); }) } as Partial<Api>);
    const store = await open(api);
    store.askUndo("change-5");
    await settled();
    expect(store.state.undoInfo["change-5"]).toMatchObject({ possible: false, text: expect.stringContaining("не подключён") });
  });

  it("снимает паузу владельца через сервис и объясняет проверку бюджета", async () => {
    const api = fakeApi({ resume: vi.fn(async () => ({ resumed: ["anna:1"], refused: [], failed: [] })) } as Partial<Api>);
    const store = await open(api);
    store.set({ paused: true });
    store.run({ action: "pause" });
    await settled();
    expect(api.pause).not.toHaveBeenCalled();
    expect(api.resume).toHaveBeenCalledOnce();
    expect(store.state.notice).toContain("Снял паузу");
    expect(store.state.notice).toContain("не выйдет за бюджет 20 000 ₽");
  });

  it("отказ включить показывает словами сервиса", async () => {
    const api = fakeApi({ resume: vi.fn(async () => { throw new ApiError(409, "Эти показы остановил не вы, а защита бюджета. Кнопка их не включит"); }) } as Partial<Api>);
    const store = await open(api);
    store.set({ paused: true });
    store.run({ action: "pause" });
    await settled();
    expect(store.state.notice).toContain("остановил не вы");
  });

  it("чат передаёт агенту последние реплики без текущего сообщения", async () => {
    const api = fakeApi({ command: vi.fn(async () => said({ reply: "Хорошо." })) } as Partial<Api>);
    const store = await open(api);
    store.send("Привет");
    await settled();
    store.send("Что посоветуешь?");
    await settled();
    const history = vi.mocked(api.command).mock.calls[1]?.[1] ?? [];
    expect(history.at(-2)).toEqual({ role: "user", text: "Привет" });
    expect(history.at(-1)).toEqual({ role: "assistant", text: "Хорошо." });
    expect(history.some((h) => h.text === "Что посоветуешь?")).toBe(false);
    expect(history.length).toBeLessThanOrEqual(10);
  });

  it("вызовы инструментов остаются у ответа и уходят в историю следующего запроса", async () => {
    const tools = [{ name: "get_budget", args: {}, result: '{"left_month_rub":10800}' }];
    const api = fakeApi({ command: vi.fn(async () => said({ reply: "Осталось 10 800 ₽.", tools })) } as Partial<Api>);
    const store = await open(api);
    store.send("Сколько осталось?");
    await settled();
    expect(store.state.msgs.at(-1)?.tools).toEqual(tools);
    store.send("А в процентах?");
    await settled();
    const history = vi.mocked(api.command).mock.calls[1]?.[1] ?? [];
    expect(history.at(-1)).toEqual({ role: "assistant", text: "Осталось 10 800 ₽.", tools });
  });

  it("чат не применяет без подтверждения и честно отказывает вне набора", async () => {
    const api = fakeApi({ command: vi.fn(async () => said({ kind: "pause", confirm: "Понял так: остановить все показы. Настройки сохранятся. Применить?" })) } as Partial<Api>);
    const store = await open(api);
    store.send("Поставь на паузу");
    await settled();
    expect(api.command).toHaveBeenCalledWith("Поставь на паузу", expect.any(Array));
    expect(api.pause).not.toHaveBeenCalled();
    expect(store.state.pending).toEqual({ action: "pause" });
    store.send("да");
    await settled();
    expect(api.command).toHaveBeenCalledOnce();
    expect(api.pause).toHaveBeenCalledOnce();
    vi.mocked(api.command).mockResolvedValueOnce(said({ reply: "Район показов меняется в разделе «Реклама». Сам ничего не изменил." }));
    store.send("Поменяй район на Арбат");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("ничего не изменил");
    expect(store.state.pending).toBeNull();
  });

  it("отказ разборщика показывает как есть", async () => {
    const api = fakeApi({ command: vi.fn(async () => { throw new ApiError(503, "Сервис занят"); }) } as Partial<Api>);
    const store = await open(api);
    store.send("пауза");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toBe("Сервис занят");
    expect(store.state.pending).toBeNull();
  });

  it("бюджет из чата: сначала подтверждение, потом сервис; ниже потраченного не предлагает", async () => {
    const api = fakeApi({ setBudget: vi.fn(async () => ({ status: "applied", cuts: 1 })) } as Partial<Api>);
    const store = await open(api);
    vi.mocked(api.command).mockResolvedValueOnce(said({ kind: "set_budget", rubles: 1000 }));
    store.send("Поставь бюджет 1000");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("уже потрачено 1 500 ₽");
    expect(store.state.pending).toBeNull();
    vi.mocked(api.command).mockResolvedValueOnce(said({ kind: "set_budget", rubles: 15000 }));
    store.send("Измени бюджет на 15000");
    await settled();
    expect(store.state.pending).toEqual({ action: "setting", value: ["budget", 15000] });
    expect(store.state.msgs.at(-1)?.text).toContain("Сначала снижу лимиты в Яндексе");
    store.send("да");
    await settled();
    expect(api.setBudget).toHaveBeenCalledWith(15000, expect.any(String));
    expect(store.state.msgs.at(-1)?.text).toContain("Бюджет месяца теперь 15 000 ₽, был 20 000 ₽");
  });

  it("две просьбы в одном сообщении: по очереди, у каждой своё «да»", async () => {
    const api = fakeApi({ setBudget: vi.fn(async () => ({ status: "applied", cuts: 1 })) } as Partial<Api>);
    const store = await open(api);
    const pause = said({ kind: "pause", confirm: "Понял так: остановить все показы. Настройки сохранятся. Применить?" });
    vi.mocked(api.command).mockResolvedValueOnce(said({ kind: "set_budget", rubles: 15000, next: [pause] }));
    store.send("бюджет 15000 и останови рекламу");
    await settled();
    expect(store.state.msgs.some((m) => m.text.includes("Вижу 2 просьбы"))).toBe(true);
    expect(store.state.pending).toEqual({ action: "setting", value: ["budget", 15000] });
    store.send("нет");
    await settled();
    expect(api.setBudget).not.toHaveBeenCalled();
    expect(store.state.pending).toEqual({ action: "pause" });
    store.send("да");
    await settled();
    expect(api.pause).toHaveBeenCalledOnce();
    expect(store.state.pending).toBeNull();
    expect(api.command).toHaveBeenCalledOnce();
  });

  it("включение из чата только после подтверждения", async () => {
    const api = fakeApi({
      resume: vi.fn(async () => ({ resumed: ["anna:1"], refused: [], failed: [] })),
      command: vi.fn(async () => said({ kind: "resume", confirm: "Понял так: снять паузу. Применить?" })),
    } as Partial<Api>);
    const store = await open(api);
    store.set({ paused: true });
    store.send("Включи рекламу");
    await settled();
    expect(api.resume).not.toHaveBeenCalled();
    expect(store.state.pending).toEqual({ action: "pause" });
  });

  it("дни без показов: сервис убирает только названные, часы направлений оставляет", async () => {
    const days = said({ kind: "skip_days", days: [7], confirm: "Понял так: не показывать рекламу: воскресенье. Бюджет месяца не изменится. Применить?" });
    const api = fakeApi({
      command: vi.fn(async () => days),
      skipDays: vi.fn(async () => ({ applied: ["anna:1"], unknown: ["anna:2"], unchanged: [] })),
    } as Partial<Api>);
    const store = await open(api);
    store.send("не показывай по воскресеньям");
    await settled();
    expect(store.state.pending).toEqual({ action: "chat", value: days });
    expect(api.skipDays).not.toHaveBeenCalled();
    store.send("да");
    await settled();
    expect(api.skipDays).toHaveBeenCalledWith([6], expect.any(String));
    const text = store.state.msgs.at(-1)?.text ?? "";
    expect(text).toContain("Больше не показываю рекламу: воскресенье");
    expect(text).toContain("У 1 направления часы ещё не прочитаны");
  });

  it("отказ сервиса по дням показывает как есть", async () => {
    const api = fakeApi({
      command: vi.fn(async () => said({ kind: "skip_days", days: [6, 7], confirm: "Понял так: … Применить?" })),
      skipDays: vi.fn(async () => { throw new ApiError(409, "Часы показа направлений ещё не прочитаны у Яндекса, поэтому дни не меняю."); }),
    } as Partial<Api>);
    const store = await open(api);
    store.send("без выходных");
    await settled();
    store.send("да");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("не прочитаны");
  });

  it("цена услуги из чата сохраняется в профиль и говорит про старые объявления", async () => {
    const outdated = [{ account: "anna", ad_id: "11", headline: "Маникюр за 1200 ₽", text: "", numbers: ["1200"], replacing: true }];
    const api = fakeApi({
      command: vi.fn(async () => said({ kind: "set_price", service: "Маникюр", rubles: 1600, confirm: "Понял так: «Маникюр» теперь 1 600 ₽. Применить?" })),
      saveProfile: vi.fn(async (p: Profile) => ({ ...p, outdated })),
    } as Partial<Api>);
    const store = await open(api);
    store.send("маникюр теперь 1600");
    await settled();
    store.send("да");
    await settled();
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({ services: [{ name: "Маникюр", price: 1600 }] }));
    const text = store.state.msgs.at(-1)?.text ?? "";
    expect(text).toContain("Цена «Маникюр» в профиле теперь 1 600 ₽");
    expect(text).toContain("«Маникюр за 1200 ₽» (1200)");
  });

  it("объявление: уже отключённое не трогает, последнее не отключает", async () => {
    const api = fakeApi({ command: vi.fn(async () => said({ kind: "ad_off", ad: { id: "anna/11", name: "Маникюр" }, confirm: "Понял так: отключить объявление «Маникюр». Применить?" })) } as Partial<Api>);
    const store = await open(api);
    store.send("отключи объявление про маникюр");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("последнее объявление");
    expect(store.state.pending).toBeNull();
    store.set({ adsOff: { "anna/11": true } });
    store.send("отключи объявление про маникюр");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("уже отключено");
  });

  it("ответ о клиентах подставляется, только если он за ту же неделю", async () => {
    const answer = { since: "2026-11-09", until: "2026-11-15", leads: 2, clients: 1 };
    const store = await open(fakeApi({ overview: vi.fn(async () => ({ ...overview(), clients: answer })) } as Partial<Api>));
    expect(store.state.clients).toBe(1);
    const old = await open(fakeApi({ overview: vi.fn(async () => ({ ...overview(), clients: { ...answer, until: "2026-11-14" } })) } as Partial<Api>));
    expect(old.state.clients).toBeNull();
  });
});

describe("лента «Сегодня» от сервиса", () => {
  const cards = [
    { kind: "paused", tone: "bad" as const, title: "Реклама на паузе", text: "Вы поставили паузу.", action: "resume" as const, ref: null, rules: "today-1" },
    { kind: "summary", tone: "info" as const, title: "12 обращений за 7 дней, по 420 ₽", text: "", action: null, ref: null, rules: "today-1" },
    { kind: "last_action", tone: "info" as const, title: "Отключил объявление", text: "Вы попросили.", action: "undo" as const, ref: "change-5", rules: "today-1" },
  ];

  it("берёт карточки сервиса в его порядке", async () => {
    const store = await open(fakeApi({ today: vi.fn(async () => ({ rules: "today-1", cards })) } as Partial<Api>));
    expect(store.state.live?.cards?.map((c) => c.kind)).toEqual(["paused", "summary", "last_action"]);
  });

  it("без ленты сервиса экран не ломается", async () => {
    const store = await open(fakeApi({ today: vi.fn(async () => { throw new ApiError(500, "сбой"); }) } as Partial<Api>));
    expect(store.state.live?.cards).toBeUndefined();
  });
});

describe("сетевые ошибки и граничные сценарии (500, 503, 401, CSRF)", () => {
  it("обрабатывает 401 Unauthorized и 403 CSRF", async () => {
    const api = fakeApi({
      pause: vi.fn(async () => { throw new ApiError(401, "Сессия истекла"); }),
      setAdState: vi.fn(async () => { throw new ApiError(403, "Неверный CSRF-токен"); }),
    } as Partial<Api>);
    const store = await open(api);

    store.run({ action: "pause" });
    await settled();
    expect(store.state.notice).toContain("Сессия истекла");

    store.run({ action: "ad", value: "anna/11" });
    await settled();
    expect(store.state.notice).toContain("Неверный CSRF-токен");
  });

  it("обрабатывает 500, 503 и сетевой сбой без ApiError", async () => {
    const api = fakeApi({
      setBudget: vi.fn(async () => { throw new ApiError(500, "Внутренняя ошибка сервиса"); }),
      rollback: vi.fn(async () => { throw new ApiError(503, "Сервис временно недоступен"); }),
      command: vi.fn(async () => { throw new TypeError("Failed to fetch"); }),
    } as Partial<Api>);
    const store = await open(api);

    store.run({ action: "setting", value: ["budget", 25000] });
    await settled();
    expect(store.state.notice).toContain("Внутренняя ошибка сервиса");

    store.run({ action: "undo", value: "change-5" });
    await settled();
    expect(store.state.notice).toContain("Сервис временно недоступен");

    store.send("любое сообщение");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("Сервис не ответил. Ничего не изменилось, попробуйте позже.");
  });

  it("сверка баланса подтверждает баланс через API", async () => {
    const api = fakeApi({ confirmBalance: vi.fn(async () => ({ confirmed: true })) } as unknown as Partial<Api>);
    const store = await open(api);
    store.run({ action: "balance" });
    await settled();
    expect(api.confirmBalance).toHaveBeenCalledWith("anna");
    expect(store.state.notice).toContain("Сверка начнётся заново");
  });

  it("ответ на предложение теста: согласие, отказ и отмена", async () => {
    const api = fakeApi({
      answerTest: vi.fn(async (_id: number, agree: boolean) => ({
        status: "ok",
        text: agree ? "Тест согласован" : "Тест отклонён",
      })),
      cancelTest: vi.fn(async () => ({ status: "cancelled", text: "Тест успешно отменён" })),
    } as Partial<Api>);
    const store = await open(api);

    store.run({ action: "test", value: "10", answer: "agree" });
    await settled();
    expect(api.answerTest).toHaveBeenCalledWith(10, true);
    expect(store.state.notice).toContain("Тест согласован");

    store.run({ action: "test", value: "10", answer: "decline" });
    await settled();
    expect(api.answerTest).toHaveBeenCalledWith(10, false);
    expect(store.state.notice).toContain("Тест отклонён");

    store.run({ action: "test", value: "10", answer: "cancel" });
    await settled();
    expect(api.cancelTest).toHaveBeenCalledWith(10);
    expect(store.state.notice).toContain("Тест успешно отменён");
  });

  it("статусы pending при отмене и переключении объявления", async () => {
    const api = fakeApi({
      rollback: vi.fn(async () => ({ status: "pending" })),
      setAdState: vi.fn(async () => ({ status: "pending" as const })),
    } as Partial<Api>);
    const store = await open(api);

    store.run({ action: "undo", value: "change-5" });
    await settled();
    expect(store.state.notice).toContain("отмена записана и будет повторена");

    store.run({ action: "ad", value: "anna/11" });
    await settled();
    expect(store.state.notice).toContain("Яндекс ещё применяет его");
  });

  it("пауза при отсутствии идущих показов и отказ по части направлений при возобновлении", async () => {
    const api = fakeApi({
      pause: vi.fn(async () => ({ stopped: [] })),
      resume: vi.fn(async () => ({ resumed: ["1"], refused: ["2"], failed: [] })),
    } as Partial<Api>);
    const store = await open(api);

    store.run({ action: "pause" });
    await settled();
    expect(store.state.notice).toContain("Идущих показов нет");

    store.set({ paused: true });
    store.run({ action: "pause" });
    await settled();
    expect(store.state.notice).toContain("Часть направлений не включил");
  });

  it("неподдерживаемое действие сообщает об этом", async () => {
    const store = await open(fakeApi());
    store.run({ action: "setting", value: ["hours" as any, "24/7"] });
    await settled();
    expect(store.state.notice).toContain("ещё не подключено к сервису");
  });

  it("команды чата: undo_last, rework_ad, history, stats, ad_on, unknown", async () => {
    const api = fakeApi({
      command: vi.fn()
        .mockResolvedValueOnce(said({ kind: "undo_last" }))
        .mockResolvedValueOnce(said({ kind: "undo_last" }))
        .mockResolvedValueOnce(said({ kind: "rework_ad", ad: { id: "anna/11", name: "Маникюр" } }))
        .mockResolvedValueOnce(said({ kind: "history" }))
        .mockResolvedValueOnce(said({ kind: "stats" }))
        .mockResolvedValueOnce(said({ kind: "ad_on", ad: { id: "anna/11", name: "Маникюр" } }))
        .mockResolvedValueOnce(said({ kind: "ad_on", ad: { id: "anna/11", name: "Маникюр" }, confirm: "Включить?" }))
        .mockResolvedValueOnce(said({ kind: "unknown", reply: "Не понял команду" })),
    } as Partial<Api>);
    const store = await open(api);

    // undo_last с доступной записью
    store.send("отмени последнее");
    await settled();
    expect(store.state.pending).toEqual({ action: "undo", value: "change-5" });

    // undo_last когда отменять нечего
    store.set({ pending: null, hist: [] });
    store.send("откатить действие");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("Отменять пока нечего");

    // rework_ad
    store.send("переделай объявление");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("Переделка — на экране объявления");
    expect(store.state.msgs.at(-1)?.link?.view).toBe("ads");

    // history: с записями — ссылка на историю (пустую историю проверяет Chat.bugs 47.4)
    store.set({ hist: [{ id: "x", kind: "money", when: "Сейчас", title: "Бюджет", why: "", next: "", canUndo: false, state: "Записано" }] });
    store.send("история");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("В истории все изменения с причинами");
    expect(store.state.msgs.at(-1)?.link?.view).toBe("history");

    // stats
    store.send("статистика");
    await settled();
    expect(store.state.msgs.at(-1)?.link?.view).toBe("stats");

    // ad_on когда уже работает
    store.set({ adsOff: {} });
    store.send("включи маникюр");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("уже работает");

    // ad_on когда отключено
    store.set({ adsOff: { "anna/11": true } });
    store.send("включи маникюр");
    await settled();
    expect(store.state.pending).toEqual({ action: "ad", value: "anna/11" });

    // unknown команда
    store.send("абракадабра");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("Не понял команду");
  });

  it("чат: повторный pause когда уже на паузе и resume когда уже активен", async () => {
    const api = fakeApi({
      command: vi.fn()
        .mockResolvedValueOnce(said({ kind: "pause" }))
        .mockResolvedValueOnce(said({ kind: "resume" })),
    } as Partial<Api>);
    const store = await open(api);

    store.set({ paused: true });
    store.send("пауза");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("Реклама уже на паузе");

    store.set({ paused: false });
    store.send("включи");
    await settled();
    expect(store.state.msgs.at(-1)?.text).toContain("Реклама и так идёт");
  });

  it("chat set_price для несуществующей услуги и skip_days без изменений", async () => {
    const api = fakeApi({
      skipDays: vi.fn(async () => ({ applied: [], unknown: [], unchanged: [] })),
    } as Partial<Api>);
    const store = await open(api);

    store.run({ action: "chat", value: said({ kind: "set_price", service: "Косметология", rubles: 2000 }) });
    await settled();
    expect(store.state.notice).toContain("уже нет в профиле");

    store.run({ action: "chat", value: said({ kind: "skip_days", days: [1] }) });
    await settled();
    expect(store.state.notice).toContain("Реклама и так не показывается");

    store.run({ action: "chat", value: said({ kind: "unknown" }) });
    await settled();
    expect(store.state.notice).toContain("ещё не подключено к сервису");
  });

  it("chat: автопилот останавливается, включается и меняет расписание теми же маршрутами", async () => {
    const stopAutopilot = vi.fn(async () => ({}) as never);
    const saveAutopilot = vi.fn(async () => ({}) as never);
    const store = await open(fakeApi({ stopAutopilot, saveAutopilot } as Partial<Api>));

    store.run({ action: "chat", value: said({ kind: "autopilot_off" }) });
    await settled();
    expect(stopAutopilot).toHaveBeenCalledTimes(1);
    expect(store.state.notice).toContain("Остановил автопилот");

    store.run({ action: "chat", value: said({ kind: "autopilot_on" }) });
    await settled();
    expect(saveAutopilot).toHaveBeenLastCalledWith({ resume: true });

    store.run({ action: "chat", value: said({ kind: "autopilot_cadence", cadence: "weekly" }) });
    await settled();
    expect(saveAutopilot).toHaveBeenLastCalledWith({ cadence: "weekly" });
    expect(store.state.notice).toContain("раз в неделю");
  });

  it("различные типы тревог (Alerts)", async () => {
    const alerts = [
      { kind: "balance_mismatch", details: {}, created_at: "" },
      { kind: "budget_stopped", details: {}, created_at: "" },
      { kind: "budget_stop_pending", details: {}, created_at: "" },
      { kind: "budget_near_limit", details: {}, created_at: "" },
      { kind: "platform_unavailable", details: {}, created_at: "" },
      { kind: "platform_not_connected", details: {}, created_at: "" },
      { kind: "ads_no_profile", details: {}, created_at: "" },
      { kind: "ads_need_confirmation", details: {}, created_at: "" },
      { kind: "ads_rejected", details: { headline: "Тест", reason: "Запрещённая тема" }, created_at: "" },
      { kind: "ads_rejected", details: {}, created_at: "" },
      { kind: "unknown_kind", details: {}, created_at: "" },
    ];
    const base = overview(alerts);
    const store = await open(fakeApi({ overview: vi.fn(async () => ({ ...base, money: [] })) } as Partial<Api>));
    expect(store.state.live?.alerts.length).toBe(alerts.length);
  });

  it("подробности открытого объявления openAdScreen и повторный askUndo", async () => {
    const api = fakeApi({
      ad: vi.fn(async () => ({
        basis: { sources: [{ fragment: "1200", label: "цена", key: "price" }], unconfirmed: [] },
      })),
    } as unknown as Partial<Api>);
    const store = await open(api);

    store.openAdScreen("invalid-id");
    expect(store.state.openAd).toBe("invalid-id");

    store.openAdScreen("anna/11");
    await settled();
    expect(api.ad).toHaveBeenCalledWith("anna", "11");
    expect(store.state.ads.find((a) => a.id === "anna/11")?.facts).toContain("Цена");

    // Повторный askUndo переключает asked в false
    store.askUndo("change-5");
    await settled();
    expect(store.state.asked["change-5"]).toBe(true);
    store.askUndo("change-5");
    expect(store.state.asked["change-5"]).toBe(false);
  });

  it("хэндлер отказа в чате отменяет ожидающее действие", async () => {
    const store = await open(fakeApi());
    store.set({ pending: { action: "pause" } });
    store.send("нет, не надо");
    expect(store.state.pending).toBeNull();
    expect(store.state.msgs.at(-1)?.text).toContain("ничего не меняю");
  });

  it("новая просьба со словом «не» при ожидающем вопросе уходит в разбор, а не считается отказом", async () => {
    const api = fakeApi();
    const store = await open(api);
    store.set({ pending: { action: "pause" } });
    vi.mocked(api.command).mockResolvedValueOnce(
      said({ kind: "skip_days", days: [6, 7], confirm: "Понял так: не показывать рекламу по выходным. Применить?" }),
    );
    store.send("Не показывай рекламу по выходным");
    await settled();
    expect(api.command).toHaveBeenCalledWith("Не показывай рекламу по выходным", expect.any(Array));
    expect(store.state.msgs.some((m) => m.text.includes("ничего не меняю"))).toBe(false);
    expect(store.state.pending?.action).not.toBe("pause");
  });

  it("в вопросе о бюджете показывает, что было", async () => {
    const api = fakeApi();
    const store = await open(api);
    const now = rub(store.state.live?.budget ?? 0);
    vi.mocked(api.command).mockResolvedValueOnce(said({ kind: "set_budget", rubles: 20000, confirm: "x" }));
    store.send("бюджет 20 тысяч");
    await settled();
    const text = store.state.msgs.at(-1)?.text ?? "";
    expect(text).toContain(`сейчас ${now}`);
  });

  it("новая просьба при неотвеченном вопросе помечает прошлый вопрос снятым", async () => {
    const api = fakeApi();
    const store = await open(api);
    store.set({ pending: { action: "pause" } });
    store.send("как дела?");
    await settled();
    expect(store.state.msgs.some((m) => m.text.includes("Прошлый вопрос не применён"))).toBe(true);
  });

  it("устаревший вопрос «Применить?» не выполняется и просит написать заново", async () => {
    vi.useFakeTimers();
    try {
      const api = fakeApi();
      const store = await open(api);
      vi.mocked(api.command).mockResolvedValueOnce(said({ kind: "pause", confirm: "Остановить все показы? Применить?", expires_in: 600 }));
      store.send("пауза");
      await vi.advanceTimersByTimeAsync(10);
      expect(store.state.pending?.action).toBe("pause");
      await vi.advanceTimersByTimeAsync(601_000);
      store.send("да");
      await vi.advanceTimersByTimeAsync(10);
      expect(api.pause).not.toHaveBeenCalled();
      expect(store.state.pending).toBeNull();
      expect(store.state.msgs.at(-1)?.text).toContain("Вопрос устарел");
    } finally {
      vi.useRealTimers();
    }
  });

  it("свежий вопрос выполняется как раньше", async () => {
    const api = fakeApi();
    const store = await open(api);
    vi.mocked(api.command).mockResolvedValueOnce(said({ kind: "pause", confirm: "Остановить? Применить?", expires_in: 600 }));
    store.send("пауза");
    await settled();
    store.send("да");
    await settled();
    expect(api.pause).toHaveBeenCalled();
  });

  it("пока сервис разбирает сообщение, чат показывает «Думаю…» и не берёт второе", async () => {
    const api = fakeApi();
    const store = await open(api);
    let release: (c: ReturnType<typeof said>) => void = () => {};
    vi.mocked(api.command).mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    store.send("как дела?");
    await settled();
    expect(store.state.thinking).toBe(true);
    store.send("и ещё вопрос");
    expect(api.command).toHaveBeenCalledTimes(1);
    release(said({ reply: "Привет" }));
    await settled();
    expect(store.state.thinking).toBe(false);
    expect(store.state.msgs.at(-1)?.text).toBe("Привет");
  });

  it("если ответа нет 15 секунд, говорит, что ничего не изменено", async () => {
    vi.useFakeTimers();
    try {
      const api = fakeApi();
      const store = await open(api);
      vi.mocked(api.command).mockImplementationOnce(() => new Promise(() => {}));
      store.send("как дела?");
      await vi.advanceTimersByTimeAsync(16_000);
      expect(store.state.msgs.some((m) => m.text.includes("Долго отвечаю. Ничего не изменено"))).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("проверяет ветки kindOf, toEntry, toAd и profile failure", async () => {
    const historyLines = [
      { ref: "change-1", at: "2026-11-01T10:00:00Z", source: "experiment", kind: "test", target: "ad:99", status: "applied", explanation: "Тест", before: {}, after: {}, rules_version: null },
      { ref: "change-2", at: "2026-11-01T10:00:00Z", source: "system", kind: "set_limit", target: "camp:1", status: "conflict", explanation: "Лимит", before: {}, after: {}, rules_version: null },
      { ref: "change-3", at: "2026-11-01T10:00:00Z", source: "system", kind: "pause", target: "camp:1", status: "failed", explanation: "Пауза", before: {}, after: {}, rules_version: null, failure: "Бюджет на период закончился" },
      { ref: "change-4", at: "2026-11-01T10:00:00Z", source: "rollback", kind: "pause_ad", target: "ad:11", status: "applied", explanation: "Откат", before: {}, after: {}, rules_version: null, reverted: true },
      { ref: "change-6", at: "2026-11-01T10:00:00Z", source: "system", kind: "create_campaign", target: "camp:1", status: "planned", explanation: "Создание", before: {}, after: {}, rules_version: null },
    ];
    const adLines = [
      line({ ad_id: "21", state: "suspended" }),
      line({ ad_id: "22", state: "moderation" }),
      line({ ad_id: "23", state: "rejected" }),
      line({ ad_id: "24", state: "archived" }),
      line({ ad_id: "25", campaign_id: "999" }), // неизвестное направление
    ];
    const api = fakeApi({
      history: vi.fn(async () => historyLines as any),
      ads: vi.fn(async () => adLines),
      profile: vi.fn(async () => { throw new Error("нет профиля"); }),
      ad: vi.fn(async () => ({ basis: { sources: [], unconfirmed: [] } })),
    } as unknown as Partial<Api>);

    const store = await open(api);
    expect(store.state.live?.profile).toEqual([]);
    expect(store.state.hist.some((h) => h.kind === "tests")).toBe(true);
    expect(store.state.hist.some((h) => h.kind === "money")).toBe(true);
    const failedPause = store.state.hist.find((h) => h.why === "Пауза");
    expect(failedPause?.next).toBe("Причина: Бюджет на период закончился");
    expect(failedPause?.state).toBe("Не удалось");
    expect(store.state.ads.some((a) => a.tag === "на проверке Яндекса")).toBe(true);
    expect(store.state.ads.some((a) => a.tag === "отклонено Яндексом")).toBe(true);

    // openAdScreen с пустыми sources
    store.openAdScreen("anna/21");
    await settled();
    expect(store.state.ads.find((a) => a.id === "anna/21")?.facts).toBe("В тексте нет фактов из профиля");

    // openAdScreen с ошибкой сети
    vi.mocked(api.ad).mockRejectedValueOnce(new Error("network error"));
    store.openAdScreen("anna/21");
    await settled();

    // askUndo с сетевой ошибкой
    vi.mocked(api.rollbackPreview).mockRejectedValueOnce(new Error("network error"));
    store.askUndo("change-2");
    await settled();
    expect(store.state.undoInfo["change-2"]?.text).toContain("Сервис не ответил");
  });

  it("проверяет ветки cuts, unknown skipDays, пустой send и liveApi", async () => {
    const api = fakeApi({
      setBudget: vi.fn().mockResolvedValueOnce({ cuts: 2 }).mockResolvedValueOnce({ cuts: 0 }),
      skipDays: vi.fn().mockResolvedValueOnce({ applied: ["anna:1"], unknown: ["anna:2", "anna:3"], unchanged: [] }),
    } as Partial<Api>);
    const store = await open(api);

    // cuts > 1
    store.run({ action: "setting", value: ["budget", 30000] });
    await settled();
    expect(store.state.notice).toContain("2 направлений");

    // cuts === 0
    store.run({ action: "setting", value: ["budget", 30000] });
    await settled();
    expect(store.state.notice).toContain("Лимиты в Яндексе не менялись");

    // skipDays с несколькими unknown
    store.run({ action: "chat", value: said({ kind: "skip_days", days: [1, 2] }) });
    await settled();
    expect(store.state.notice).toContain("2 направлений");

    // пустой ввод
    store.send("   ");
    expect(store.state.msgs.at(-1)?.who).not.toBe("me");

    // liveApi
    expect(liveApi(store)).toBe(api);
    expect(liveApi(new MockStore())).toBeNull();

    // skipDays с ровно 1 unknown направлением
    vi.mocked(api.skipDays).mockResolvedValueOnce({ applied: ["anna:1"], unknown: ["anna:2"], unchanged: [] });
    store.run({ action: "chat", value: said({ kind: "skip_days", days: [1] }) });
    await settled();
    expect(store.state.notice).toContain("1 направления");

    // set_price с null в c.rubles
    vi.mocked(api.profile).mockResolvedValueOnce({ niche: "маникюр", address: "Москва", services: [{ name: "Маникюр", price: 1000 }], features: [], website: "" });
    vi.mocked(api.saveProfile).mockResolvedValueOnce({ niche: "маникюр", address: "Москва", services: [{ name: "Маникюр", price: 1000 }], features: [], website: "", outdated: [] });
    store.run({ action: "chat", value: said({ kind: "set_price", service: "Маникюр", rubles: null }) });
    await settled();
    expect(store.state.notice).toContain("0 ₽");

    // finish при state.mode === "chat" (notice остаётся пустым)
    store.set({ mode: "chat", notice: "" });
    store.run({ action: "setting", value: ["budget", 30000] });
    await settled();
    expect(store.state.notice).toBe("");

    // Очередь команд: c.next не пустой
    const c2 = said({ kind: "pause", confirm: "Остановить все показы?" });
    const c1 = said({ kind: "set_budget", rubles: 25000, confirm: "Поставить бюджет 25 000 ₽?", next: [c2] });
    vi.mocked(api.command).mockResolvedValueOnce(c1);
    store.send("сделай бюджет 25000 и поставь на паузу");
    await settled();
    expect(store.state.msgs.some((m) => m.text.includes("Вижу 2 просьбы"))).toBe(true);
    // Отказываемся от первой, переходит ко второй
    store.send("нет");
    await settled();
    expect(store.state.msgs.some((m) => m.text.includes("Хорошо, это не меняю"))).toBe(true);
    expect(store.state.pending?.action).toBe("pause");
    // Подтверждаем вторую
    store.send("да");
    await settled();
  });

  it("проверяет savedText и summary в граничных случаях", async () => {
    expect(savedText([])).toContain("в уже идущих объявлениях старых цифр нет");
    const laterAd = { account: "anna", ad_id: "1", headline: "Текст", text: "", numbers: ["100"], replacing: false };
    const nowAd = { account: "anna", ad_id: "2", headline: "Текст 2", text: "", numbers: ["200"], replacing: true };
    expect(savedText([laterAd])).toContain("лимит текстов на сегодня исчерпан");
    expect(savedText([nowAd, laterAd])).toContain("Пишу замену");

    // summary при нулях показов и трат
    const zeroOverview = overview();
    zeroOverview.days = [];
    const storeZero = await open(fakeApi({ overview: vi.fn(async () => zeroOverview) } as Partial<Api>));
    expect(storeZero.state.msgs[0]?.text).toContain("показов и трат не было");

    // summary при тратах без обращений
    const spendNoLeads = overview();
    spendNoLeads.days = [{ day: "2026-11-15", totals: { impressions: 10, clicks: 1, cost: 500 * 1_000_000, conversions: 0 } }];
    const storeSpend = await open(fakeApi({ overview: vi.fn(async () => spendNoLeads) } as Partial<Api>));
    expect(storeSpend.state.msgs[0]?.text).toContain("обращений пока нет");
  });

  it("проверяет оставшиеся ветки perform, undo, ad_off, higher budget и подтверждения", async () => {
    const store = await open(fakeApi());

    // default в perform
    store.run({ action: "price", value: 1500 });
    await settled();
    expect(store.state.notice).toContain("ещё не подключено к сервису");

    // undo без undoInfo и без записи в истории
    store.set({ hist: [], undoInfo: {} });
    store.run({ action: "undo", value: "change-99", whole: true });
    await settled();
    expect(store.state.notice).toContain("направлений: изменение");

    // ad без найденного объявления
    store.set({ ads: [] });
    store.run({ action: "ad", value: "anna/99" });
    await settled();
    expect(store.state.notice).toContain("«»");

    // set_budget с суммой выше текущего бюджета (lower = false)
    const apiHigh = fakeApi({
      command: vi.fn(async () => said({ kind: "set_budget", rubles: 25000 })),
    } as Partial<Api>);
    const storeHigh = await open(apiHigh);
    storeHigh.send("поставь бюджет 25000");
    await settled();
    expect(storeHigh.state.msgs.at(-1)?.text).not.toContain("Сначала снижу");

    // ad_off когда есть другие активные объявления (refusal = false)
    const twoAds = [line({ ad_id: "1" }), line({ ad_id: "2" })];
    const apiTwo = fakeApi({
      ads: vi.fn(async () => twoAds),
      command: vi.fn(async () => said({ kind: "ad_off", ad: { id: "anna/1", name: "Объявление 1" }, confirm: "Отключить объявление 1?" })),
    } as Partial<Api>);
    const storeTwo = await open(apiTwo);
    storeTwo.send("отключи объявление 1");
    await settled();
    expect(storeTwo.state.pending).toEqual({ action: "ad", value: "anna/1" });

    // подтверждение в чате через "да"
    storeTwo.send("да, подтверждаю");
    await settled();
    expect(storeTwo.state.pending).toBeNull();

    // finish с обычной ошибкой сети (не ApiError)
    vi.mocked(apiTwo.pause).mockRejectedValueOnce(new Error("Generic network failure"));
    storeTwo.run({ action: "pause" });
    await settled();
    expect(storeTwo.state.notice).toContain("Сервис не ответил");

    // set_budget без указания рублей (rubles: null)
    vi.mocked(apiTwo.command).mockResolvedValueOnce(said({ kind: "set_budget", rubles: null }));
    storeTwo.send("поставь бюджет");
    await settled();
    expect(storeTwo.state.msgs.at(-1)?.text).toContain("уже потрачено");

    // set_price без указания рублей (rubles: null)
    vi.mocked(apiTwo.command).mockResolvedValueOnce(said({ kind: "set_price", service: "Маникюр", rubles: null, confirm: "Маникюр без цены?" }));
    storeTwo.send("маникюр бесплатно");
    await settled();
    storeTwo.send("да");
    await settled();
  });
});
