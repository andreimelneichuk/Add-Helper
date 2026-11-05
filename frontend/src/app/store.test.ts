import { describe, expect, it } from "vitest";
import { initialState, Store, week } from "./store";

describe("хранилище прототипа", () => {
  it("чат меняет только после подтверждения и пишет в историю", () => {
    const store = new Store();
    const before = store.state.hist.length;
    store.send("Не показывай по воскресеньям");
    expect(store.state.f.hours).toBe("в часы работы");
    store.send("Применить");
    expect(store.state.f.hours).toBe("кроме воскресений");
    expect(store.state.hist).toHaveLength(before + 1);
  });

  it("отмена не стирает исходную запись", () => {
    const store = new Store();
    const before = store.state.hist.length;
    store.run({ action: "undo", value: "off" });
    expect(store.state.undone.off).toBe(true);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(store.state.hist.some((h) => h.id === "off")).toBe(true);
  });

  it("итог на панели показывается полосой, в чате не показывается", () => {
    const store = new Store();
    store.run({ action: "pause" });
    expect(store.state.notice).toContain("паузу");
    store.openChat();
    store.run({ action: "pause" }, "Включи");
    expect(store.state.notice).toBe("");
  });

  it("запуск не идёт дальше без кабинета, ИНН и кода", () => {
    const store = new Store(initialState("start"));
    store.set({ site: "salon-primer.ru", step: 4 });
    store.next();
    expect(store.state.step).toBe(4);
    expect(store.state.block).not.toBe("");
    store.set({ yandex: true, inn: "7700000000", metrica: true });
    store.next();
    store.next();
    store.next();
    expect(store.state.step).toBe(7);
    store.next();
    expect(store.state.stage).toBe("work");
  });

  it("запуск из чата не проходит, если сняты все объявления", () => {
    const store = new Store(initialState("start"));
    store.set({ step: 3, draftOff: { a1: true, a2: true, a3: true } });
    store.send("нравится");
    expect(store.state.step).toBe(3);
    expect(store.state.block).not.toBe("");
  });

  it.each(["не применяй", "не давай", "когда начнётся"])("фраза «%s» не подтверждает действие", (phrase) => {
    const store = new Store();
    store.send("Бюджет 30000");
    store.send(phrase);
    expect(store.state.f.budget).toBe(20000);
  });

  it("считает неделю по подставным данным", () => {
    expect(week(initialState())).toMatchObject({ cost: 5040, leads: 12, prevCost: 3360, prevLeads: 6 });
  });
});
