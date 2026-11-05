import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { Api, Profile } from "../api";
import { fakeApi, line, open, overview, said, settled } from "./testLive";
import { rub } from "./data";
import { statusText, week } from "./store";

describe("живые данные", () => {
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
    expect(store.state.hist[0]).toMatchObject({ kind: "yours", canUndo: true, about: "anna/11" });
  });

  it("объявление с картинкой показывает её, без картинки — только текст", async () => {
    const api = fakeApi({ ads: vi.fn(async () => [line({ image_id: 7 }), line({ ad_id: "12", image_id: null })]) } as Partial<Api>);
    const store = await open(api);
    expect(store.state.ads.find((a) => a.id === "anna/11")?.photo).toBe("/api/businesses/b1/photos/7");
    expect(store.state.ads.find((a) => a.id === "anna/12")?.photo).toBeUndefined();
  });

  it("тревогу показывает в состоянии", async () => {
    const alert = { kind: "zero_balance", details: {}, created_at: "2026-11-15T00:00:00Z" };
    const store = await open(fakeApi({ overview: vi.fn(async () => overview([alert])) } as Partial<Api>));
    expect(statusText(store.state)).toBe("Показы остановлены");
  });
});

describe("действия идут через сервис", () => {
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

  it("чат не применяет без подтверждения и честно отказывает вне набора", async () => {
    const api = fakeApi({ command: vi.fn(async () => said({ kind: "pause", confirm: "Понял так: остановить все показы. Настройки сохранятся. Применить?" })) } as Partial<Api>);
    const store = await open(api);
    store.send("Поставь на паузу");
    await settled();
    expect(api.command).toHaveBeenCalledWith("Поставь на паузу");
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
