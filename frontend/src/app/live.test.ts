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

  it("снять паузу честно отказывается", async () => {
    const api = fakeApi();
    const store = await open(api);
    store.set({ paused: true });
    store.run({ action: "pause" });
    await settled();
    expect(api.pause).not.toHaveBeenCalled();
    expect(store.state.notice).toContain("нельзя");
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
    store.send("Измени бюджет на 30000");
    expect(store.state.msgs.at(-1)?.text).toContain("ничего не изменил");
  });
});
