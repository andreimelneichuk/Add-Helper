import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { Api } from "../api";
import { fakeApi, open, overview, settled } from "./testLive";
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
    expect(api.rollback).toHaveBeenCalledWith(5);
    expect(store.state.undone["change-5"]).toBe(true);
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

  it("чат не применяет без подтверждения и не обещает неподключённое", async () => {
    const api = fakeApi();
    const store = await open(api);
    store.send("Поставь на паузу");
    expect(api.pause).not.toHaveBeenCalled();
    expect(store.state.pending).toEqual({ action: "pause" });
    store.send("да");
    await settled();
    expect(api.pause).toHaveBeenCalledOnce();
    store.send("Поменяй район на Арбат");
    expect(store.state.msgs.at(-1)?.text).toContain("ничего не изменил");
  });

  it("бюджет из чата: сначала подтверждение, потом сервис; ниже потраченного не предлагает", async () => {
    const api = fakeApi({ setBudget: vi.fn(async () => ({ status: "applied", cuts: 1 })) } as Partial<Api>);
    const store = await open(api);
    store.send("Поставь бюджет 1000");
    expect(store.state.msgs.at(-1)?.text).toContain("уже потрачено 1 500 ₽");
    expect(store.state.pending).toBeNull();
    store.send("Измени бюджет на 15000");
    expect(store.state.pending).toEqual({ action: "setting", value: ["budget", 15000] });
    expect(store.state.msgs.at(-1)?.text).toContain("Сначала снижу лимиты в Яндексе");
    store.send("да");
    await settled();
    expect(api.setBudget).toHaveBeenCalledWith(15000, expect.any(String));
    expect(store.state.msgs.at(-1)?.text).toContain("Бюджет месяца теперь 15 000 ₽, был 20 000 ₽");
  });

  it("включение из чата только после подтверждения", async () => {
    const api = fakeApi({ resume: vi.fn(async () => ({ resumed: ["anna:1"], refused: [], failed: [] })) } as Partial<Api>);
    const store = await open(api);
    store.set({ paused: true });
    store.send("Включи рекламу");
    expect(api.resume).not.toHaveBeenCalled();
    expect(store.state.pending).toEqual({ action: "pause" });
  });

  it("ответ о клиентах подставляется, только если он за ту же неделю", async () => {
    const answer = { since: "2026-11-09", until: "2026-11-15", leads: 2, clients: 1 };
    const store = await open(fakeApi({ overview: vi.fn(async () => ({ ...overview(), clients: answer })) } as Partial<Api>));
    expect(store.state.clients).toBe(1);
    const old = await open(fakeApi({ overview: vi.fn(async () => ({ ...overview(), clients: { ...answer, until: "2026-11-14" } })) } as Partial<Api>));
    expect(old.state.clients).toBeNull();
  });
});
