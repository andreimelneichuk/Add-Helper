import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Api } from "../api";
import { fakeApi, MILLION, open, overview } from "./testLive";

// Лимиты каждого сайта и вызовы /budget: по ним видно порядок изменений.
const limits: Record<string, number> = {};
const calls: { id: string; rubles: number }[] = [];
let failOn: string | null = null;

function siteApi(id: string): Api {
  return {
    ...fakeApi(),
    overview: vi.fn(async () => {
      const o = overview();
      o.money[0].period.hard_limit = (limits[id] ?? 0) * MILLION;
      return o;
    }),
    setBudget: vi.fn(async (rubles: number) => {
      if (failOn === id) throw new Error("Директ не ответил");
      calls.push({ id, rubles });
      limits[id] = rubles;
      return { status: "applied" } as never;
    }),
  } as Api;
}

vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return {
    ...real,
    openSession: vi.fn(async () => ({
      me: { csrf_token: "t" },
      businesses: [{ id: "a", name: "Салон" }, { id: "b", name: "Бигуди" }, { id: "c", name: "Новый" }],
    })),
    createApi: vi.fn((id: string) => siteApi(id)),
  };
});

describe("общий бюджет на все сайты", () => {
  beforeEach(() => {
    calls.length = 0;
    failOn = null;
    Object.assign(limits, { a: 10000, b: 30000, c: 0 });
  });

  async function store() {
    const s = await open(fakeApi({
      saveBudgetPortfolio: vi.fn(async (body: { mode: string; rubles?: number }) => ({
        mode: body.mode as "shared", rubles: body.rubles ?? null,
        shares: [{ business_id: "a", rubles: 20000 }, { business_id: "b", rubles: 20000 }, { business_id: "c", rubles: 20000 }],
      })),
    } as Partial<Api>));
    return s;
  }

  it("сначала снижает, потом повышает и не трогает сайт без запущенного периода", async () => {
    const s = await store();
    const text = await s.applySharedBudget(60000);
    expect(calls).toEqual([{ id: "b", rubles: 20000 }, { id: "a", rubles: 20000 }]);
    expect(text).toContain("Режим хранится в сервисе");
  });

  it("останавливается на первой ошибке и называет, что уже изменено", async () => {
    const s = await store();
    failOn = "a";
    await expect(s.applySharedBudget(60000)).rejects.toThrow(/Остановился на сайте «Салон».*Уже изменил: «Бигуди»/);
    expect(calls).toEqual([{ id: "b", rubles: 20000 }]);
  });
});
