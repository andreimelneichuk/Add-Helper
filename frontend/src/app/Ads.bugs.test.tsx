import { MockStore } from "../mock/MockStore";
import { AdDetail } from "./screens/AdDetail";
import { initialState } from "./store";
import { fakeApi, open } from "./testLive";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { GenerateAdsModal } = await import("./screens/ads/GenerateAdsModal");
const { Start } = await import("./screens/Start");

const guest = vi.hoisted(() => ({
  writeAds: vi.fn(),
  adSet: vi.fn(),
  budgetOptions: vi.fn(async () => ({ min: 1667, max: 1_000_000, presets: [] })),
}));

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, createGuestApi: () => ({ ...real.createGuestApi(), ...guest }) };
});
describe("Объявления и генерация: регрессии (BUG-…)", () => {
  beforeEach(() => {
    guest.writeAds.mockReset();
    guest.adSet.mockReset();
  });

  describe("BUG-036: AdDetail refresh after rework", () => {
    it("36.1: refreshes store when reworked ad is applied", async () => {
      const api = fakeApi();
      const store = await open(api);
      const refreshSpy = vi.spyOn(store, "refresh");

      const ad = store.state.ads[0]!;
      store.rework = {
        rework: vi.fn(async () => ({ id: 42 })),
        reworkSet: vi.fn(async () => ({
          status: "ready",
          variants: [{ id: 101, headline: "Новый заголовок", text: "Новый текст", sources: [] }],
        })),
        chooseRework: vi.fn(async () => ({ status: "ready", text: "Вариант поставлен в показы." })),
      } as any;

      render(<AdDetail store={store} id={ad.id} />);

      // Open rework
      const reworkBtn = screen.getByRole("button", { name: "Переделать" });
      await userEvent.click(reworkBtn);

      // Trigger "Короче"
      const chip = screen.getByRole("button", { name: "Короче" });
      await userEvent.click(chip);

      // Wait for variants and click "Поставить в показы"
      const submitBtn = await screen.findByRole("button", { name: "Поставить в показы" });
      await userEvent.click(submitBtn);

      // Verify store.refresh was called
      await waitFor(() => {
        expect(refreshSpy).toHaveBeenCalled();
      });
    });
  });

  describe("BUG-022: Sidebar ads count retained after navigating back", () => {
    it("22.1: sidebar shows ads count when ads exist even after stepping back", () => {
      const store = new MockStore(initialState("start"));
      store.set({
        site: "salon.ru",
        step: 4,
        ads: [{ tag: "", tagTone: "accent", leads: 0, cost: 0, id: "a1", name: "Реклама 1", title: "Заголовок 1", hook: "Крючок", text: "Текст", facts: "Факты" }],
        draftOff: {},
      });
      // Step back to step 2
      store.set({ step: 2 });
      render(<Start store={store} />);
      const side = screen.getByRole("complementary", { name: "Что уже готово" });
      expect(side).toHaveTextContent("1 готово");
    });
  });

  describe("BUG-026: Ad cards visual distinction", () => {
    it("26.1: renders explicit status badges for kept and dropped ads", () => {
      const store = new MockStore(initialState("start"));
      store.set({
        step: 4,
        ads: [
          { tag: "", tagTone: "accent", leads: 0, cost: 0, id: "a1", name: "Объявление 1", title: "Объявление 1", hook: "Крючок", text: "Текст 1", facts: "Факты" },
          { tag: "", tagTone: "accent", leads: 0, cost: 0, id: "a2", name: "Объявление 2", title: "Объявление 2", hook: "Крючок", text: "Текст 2", facts: "Факты" },
        ],
        draftOff: { a2: true },
      });
      render(<Start store={store} />);
      expect(screen.getByText("В рекламу")).toBeInTheDocument();
      expect(screen.getByText("Убрано")).toBeInTheDocument();
    });
  });

  describe("BUG-028: Ads limit recovery", () => {
    it("28.1: allows proceeding to cabinet step when ad generation limit is reached", () => {
      const store = new MockStore(initialState("start"));
      store.writer = {} as any;
      store.set({
        step: 4,
        ads: [],
        block: "Лимит на генерацию объявлений исчерпан. Попробуйте позже.",
      });
      store.next();
      expect(store.state.step).toBe(5);
    });
  });

  describe("BUG-040: 'Написать другие варианты' requests fresh set with force=true", () => {
    it("40.1: passes force: true when requesting other variants", async () => {
      const store = new MockStore(initialState("start"));
      let capturedProfile: any = null;
      store.writer = {
        writeAds: async (p: unknown) => { capturedProfile = p; return { id: 10, token: "tok", status: "ready" }; },
        adSet: async () => ({ id: 10, status: "ready", source: "model", template: false, needs_review: false, problem_text: null, ads: [] }),
      } as any;
      store.set({ step: 4, ads: [{ tag: "", tagTone: "accent", leads: 0, cost: 0, id: "a1", name: "Реклама 1", title: "Заголовок", hook: "", text: "", facts: "" }] });
      await store.writeAds(true);
      expect(capturedProfile?.force).toBe(true);
    });
  });

  describe("BUG-045: генерация в кабинете берёт факты только из профиля", () => {
    it("45.1: при пустом профиле отказывает и не зовёт писателя", async () => {
      const api = fakeApi({ profile: vi.fn(async () => ({ services: [], address: "", features: [], niche: "", website: "" })) });
      const store = await open(api);
      render(<GenerateAdsModal isOpen onClose={vi.fn()} store={store} />);
      expect(screen.getByRole("alert")).toHaveTextContent("В профиле нет услуг");
      expect(screen.queryByRole("button", { name: "Стрижка" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Сгенерировать через ИИ" })).toBeNull();
      expect(guest.writeAds).not.toHaveBeenCalled();
    });

    it("45.2: отправляет писателю профиль бизнеса, а не выдуманный адрес", async () => {
      const api = fakeApi();
      const store = await open(api);
      guest.writeAds.mockResolvedValue({ id: 7, token: "t", status: "queued" });
      guest.adSet.mockResolvedValue({ id: 7, status: "ready", ads: [{ headline: "Маникюр за 1 200 ₽", text: "Москва.", facts: ["Цена"] }] });
      render(<GenerateAdsModal isOpen onClose={vi.fn()} store={store} />);
      await userEvent.click(screen.getByRole("button", { name: "Сгенерировать через ИИ" }));
      await screen.findByText("Маникюр за 1 200 ₽");
      expect(guest.writeAds).toHaveBeenCalledWith({ niche: "", address: "Москва", services: [{ name: "Маникюр", price: 1200 }], features: [], variants: 3 });
    });

    it("45.3: при сбое писателя не подставляет шаблон с выдуманными фактами", async () => {
      const api = fakeApi();
      const store = await open(api);
      guest.writeAds.mockRejectedValue(new Error("Сервис не ответил"));
      render(<GenerateAdsModal isOpen onClose={vi.fn()} store={store} />);
      await userEvent.click(screen.getByRole("button", { name: "Сгенерировать через ИИ" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Сервис не ответил");
      expect(screen.queryByText(/онлайн-запись|21:00/)).toBeNull();
    });

    it("45.4: выбранные объявления принимает сервис, а не локальный список", async () => {
      const adoptAds = vi.fn(async () => ({ kept: 1 }));
      const api = fakeApi({ adoptAds });
      const store = await open(api);
      guest.writeAds.mockResolvedValue({ id: 7, token: "t", status: "queued" });
      guest.adSet.mockResolvedValue({ id: 7, status: "ready", ads: [{ headline: "Маникюр за 1 200 ₽", text: "Москва.", facts: ["Цена"] }] });
      const onClose = vi.fn();
      render(<GenerateAdsModal isOpen onClose={onClose} store={store} />);
      await userEvent.click(screen.getByRole("button", { name: "Сгенерировать через ИИ" }));
      await userEvent.click(await screen.findByRole("button", { name: /Добавить выбранные/ }));
      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(adoptAds).toHaveBeenCalledWith({ id: 7, token: "t", kept: [0] });
      expect(store.state.ads.some((a) => a.id.startsWith("gen-"))).toBe(false);
    });
  });
});
