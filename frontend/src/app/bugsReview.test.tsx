import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Api } from "../api";
import { fakeApi, open, overview, settled } from "./testLive";
import { initialState, Store, useStoreState, draftAd } from "./store";
import { Clients } from "./screens/overview/Clients";
import { DayChart } from "./screens/overview/DayChart";
import { LiveSettings } from "./screens/LiveSettings";
import { Chat } from "./screens/Chat";

function LiveClients({ store }: { store: Store }) {
  return <Clients s={useStoreState(store)} store={store} />;
}

describe("Code Review Reproduction & Fix Verification Tests", () => {
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
      const store = new Store(initialState());
      store.run({ action: "clients", value: 0 });
      // Fixed: notice says "Клиентов пока нет." without Infinity / ∞
      expect(store.state.notice).toContain("Клиентов пока нет.");
      expect(store.state.notice).not.toContain("∞");
    });
  });

  describe("BUG-015: DayChart.tsx renders correctly when days array is empty", () => {
    it("15.1: does not render '0 ноября' when days is empty", () => {
      const store = new Store(initialState());
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

  describe("BUG-016: LiveSettings.tsx Photos consent is reset after photo upload", () => {
    it("16.1: consent resets to false after upload", async () => {
      const api = fakeApi({
        photos: vi.fn(async () => ({ photos: [], max: 5 })),
        uploadPhoto: vi.fn(async () => ({ id: 1, url: "/photo1.jpg" })),
      } as unknown as Partial<Api>);
      const store = await open(api);
      render(<LiveSettings store={store} />);

      const consentBox = await screen.findByRole("checkbox", { name: /Люди на фото согласны/ });
      expect(consentBox).not.toBeChecked();

      // Check consent
      await userEvent.click(consentBox);
      expect(consentBox).toBeChecked();

      // Upload file
      const fileInput = screen.getByLabelText("Файл фото");
      const file = new File(["dummy"], "photo.jpg", { type: "image/jpeg" });
      await userEvent.upload(fileInput, file);
      await settled();

      expect(api.uploadPhoto).toHaveBeenCalledWith(file, true);

      // Fixed: consent checkbox is reset to unchecked
      expect(consentBox).not.toBeChecked();
    });
  });

  describe("BUG-017: Chat.tsx chips suggest 'Включи рекламу' when already paused", () => {
    it("17.1: shows 'Включи рекламу' chip when s.paused is true", async () => {
      const api = fakeApi({
        overview: vi.fn(async () => overview()),
      } as unknown as Partial<Api>);
      const store = await open(api);
      store.set({ paused: true, mode: "chat" });

      render(<Chat store={store} />);
      // Fixed: It shows "Включи рекламу" button instead of "Поставь на паузу"
      expect(screen.getByRole("button", { name: "Включи рекламу" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Поставь на паузу" })).not.toBeInTheDocument();
    });
  });

  describe("BUG-018: askRefusal in store.ts supports user-added promo facts without f.id === 'promo'", () => {
    it("18.1: allows discount request when user added a promo fact with custom id", () => {
      const s = initialState();
      s.facts = [
        { id: "what", field: "niche", label: "Чем занимаетесь", value: "Маникюр", source: "с сайта" },
        { id: "where", field: "address", label: "Где", value: "Москва", source: "с сайта" },
        { id: "hours", field: "feature", label: "Когда", value: "Каждый день", source: "с сайта" },
        // User added a promo fact!
        { id: "n1", field: "feature", label: "Акция", value: "Скидка 20% на первый визит", source: "от вас" },
      ];

      // Request an ad about the promo/discount
      const result = draftAd(s, null, "акция");
      // Fixed: Generates promo ad successfully, no refusal
      expect(result).toHaveProperty("ad");
      if ("ad" in result) {
        expect(result.ad.hook).toContain("первый визит");
      }
    });
  });
});
