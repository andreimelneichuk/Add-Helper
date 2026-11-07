import type { Api } from "../api";
import { MockStore } from "../mock/MockStore";
import { Overview } from "./screens/Overview";
import { Clients } from "./screens/overview/Clients";
import { DayChart } from "./screens/overview/DayChart";
import { Store, initialState, useStoreState } from "./store";
import { fakeApi, open, overview, settled } from "./testLive";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

function LiveClients({ store }: { store: Store }) {
  return <Clients s={useStoreState(store)} store={store} />;
}

describe("Обзор: клиенты и график дня: регрессии (BUG-…)", () => {
  describe("BUG-014: Clients.tsx / store.ts: 0 leads deadlock & 0 clients rejection / division by zero", () => {
    it("14.1: does not render question when leads === 0", async () => {
      const answerClients = vi.fn();
      const api = fakeApi({
        overview: vi.fn(async () => ({
          ...overview(),
          days: [{ day: "2026-11-15", totals: { impressions: 10, clicks: 1, cost: 100_000_000, conversions: 0 } }],
          totals: { impressions: 10, clicks: 1, cost: 100_000_000, conversions: 0 },
        })),
        ads: vi.fn(async () => []),
        answerClients,
      } as unknown as Partial<Api>);
      const store = await open(api);
      render(<LiveClients store={store} />);

      // Fixed: When leads === 0, the question is NOT rendered
      expect(screen.queryByText(/Сколько из/)).not.toBeInTheDocument();
    });

    it("14.2: successfully records 0 clients when leads > 0 without division by zero", async () => {
      const answerClients = vi.fn().mockResolvedValue({ since: "2026-11-09", until: "2026-11-15", leads: 5, clients: 0 });
      const api = fakeApi({
        overview: vi.fn(async () => ({
          ...overview(),
          days: [{ day: "2026-11-15", totals: { impressions: 10, clicks: 2, cost: 100_000_000, conversions: 5 } }],
          totals: { impressions: 10, clicks: 2, cost: 100_000_000, conversions: 5 },
        })),
        ads: vi.fn(async () => []),
        answerClients,
      } as unknown as Partial<Api>);
      const store = await open(api);
      render(<LiveClients store={store} />);

      await userEvent.type(screen.getByRole("textbox"), "0");
      await userEvent.click(screen.getByRole("button", { name: "Записать" }));
      await settled();

      // Fixed: answerClients called with 0
      expect(answerClients).toHaveBeenCalledWith({ since: "2026-11-09", until: "2026-11-15" }, 0);
      expect(store.state.notice).toContain("Клиентов пока нет");
      expect(store.state.notice).not.toContain("Infinity");
      expect(store.state.notice).not.toContain("∞");
    });

    it("14.3: when s.clients === 0, Clients.tsx correctly shows saved message", async () => {
      const api = fakeApi({
        overview: vi.fn(async () => ({
          ...overview(),
          clients: { since: "2026-11-09", until: "2026-11-15", leads: 2, clients: 0 },
        })),
      } as unknown as Partial<Api>);
      const store = await open(api);
      expect(store.state.clients).toBe(0);

      render(<LiveClients store={store} />);
      // Fixed: Shows "Записал 0 клиентов. Клиентов пока нет." and no textbox
      expect(screen.getByText(/Записал 0 клиентов/)).toBeInTheDocument();
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    });

    it("14.4: action clients with value 0 handles division by zero safely", () => {
      const store = new MockStore(initialState());
      store.run({ action: "clients", value: 0 });
      // Fixed: notice says "Клиентов пока нет." without Infinity / ∞
      expect(store.state.notice).toContain("Клиентов пока нет.");
      expect(store.state.notice).not.toContain("∞");
    });
  });

  describe("BUG-015: DayChart.tsx renders correctly when days array is empty", () => {
    it("15.1: does not render '0 ноября' when days is empty", () => {
      const store = new MockStore(initialState());
      // Empty days
      store.set({
        live: {
          name: "Салон",
          today: "2026-11-01",
          monthName: "ноябрь",
          monthOf: "ноября",
          monthDays: 30,
          days: [],
          trail: [],
          spent: 0,
          budget: 20000,
          balance: null,
          gap: null,
          account: null,
          hasCampaigns: true,
          alerts: [],
          stats: {} as any,
          profile: [],
        },
      });

      render(<DayChart s={store.state} store={store} />);
      // Fixed: Note does NOT contain "0 ноября:"
      expect(screen.queryByText(/0 ноября:/)).not.toBeInTheDocument();
      expect(screen.getByText(/Показов в этом месяце ещё не было/)).toBeInTheDocument();
    });
  });

  describe("BUG-029: DayChart with zero budget", () => {
    it("29.1: does not show fake 1000 ₽ scale and explains budget is not set", () => {
      const store = new MockStore(initialState());
      const s = {
        ...initialState(),
        f: { ...initialState().f, budget: 0 },
        live: { budget: 0, spent: 0, days: [] } as any,
      };
      render(<DayChart s={s} store={store} />);
      expect(screen.queryByText("1 000 ₽")).not.toBeInTheDocument();
      expect(screen.getByText(/Бюджет на месяц ещё не задан/i)).toBeInTheDocument();
    });
  });

  describe("BUG-037: Overview shows empty state when ads/profile are empty", () => {
    it("37.1: renders helpful message when no ads or profile facts exist", () => {
      const store = new MockStore(initialState());
      store.set({ ads: [] });
      render(<Overview store={store} />);
      expect(screen.getByText(/Объявлений пока нет\. Заполните услуги в профиле/i)).toBeInTheDocument();
    });
  });
});
