import { MockStore } from "../mock/MockStore";
import { AppShell } from "./AppShell";
import { clearDraft, loadDraft, saveDraft } from "./draft";
import { Store, initialState } from "./store";
import type { WizardApi } from "./store";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
const facts = [{ id: "s1", field: "service" as const, label: "Услуга", value: "Маникюр, 900 ₽", source: "от вас" }];
const wizardApi = () => ({
  accounts: vi.fn(async () => []),
  connect: vi.fn(),
  marking: vi.fn(async () => ({ saved: null })),
  saveMarking: vi.fn(),
  tagCheck: vi.fn(async () => ({ check: null, site: "" })),
  requestTagCheck: vi.fn(),
  launchCheck: vi.fn(async () => ({ items: [], ready: false, launch_available: false, launch_text: "" })),
  wizardAds: vi.fn(async () => ({ ads: [] })),
}) as unknown as WizardApi;

/** Мастер нового сайта у владельца, который уже вошёл и у которого есть другой бизнес. */
function signedWizard(step: number) {
  clearDraft();
  const store = new MockStore({ ...initialState("start"), ads: [], facts, site: "", step, account: { email: "anna@example.ru" }, businesses: [{ id: "old", name: "Бигуди Пермь" }] });
  store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
  store.writer = { writeAds: vi.fn(), adSet: vi.fn() };
  store.navigate = vi.fn();
  store.waitMs = 1;
  const business = { saveProfile: vi.fn(async () => ({})), saveBudgetPlan: vi.fn(async () => ({ rubles: 15000 })), adoptAds: vi.fn() };
  const deps = {
    session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [{ id: "old", name: "Бигуди Пермь" }] })),
    createBusiness: vi.fn(async () => ({ id: "new", name: "Маникюр" })),
    business: vi.fn(() => business),
    open: vi.fn(() => wizardApi()),
  };
  store.transferDeps = deps as unknown as NonNullable<Store["transferDeps"]>;
  render(<Start store={store} />);
  return { store, deps, business, user: userEvent.setup() };
}
describe("Мастер и первый запуск: регрессии (BUG-…)", () => {
  beforeEach(() => {
    guest.writeAds.mockReset();
    guest.adSet.mockReset();
  });

  describe("BUG-034: Step 5 marking validation in real onboarding mode", () => {
    it("34.1: rejects invalid/empty INN and empty name without calling wizard.saveMarking", async () => {
      const saveMarking = vi.fn();
      const store = new MockStore(initialState("start"));
      // Simulate real onboarding mode
      store.reader = { readSite: vi.fn(), siteRead: vi.fn() } as any;
      store.wizard = { saveMarking } as any;

      // Navigate to step 5 (Marking)
      store.set({ step: 5, inn: "", advertiser: { kind: "legal", name: "", phone: "", okved: "96.02" } });

      store.next();

      // wizard.saveMarking must NOT have been called
      expect(saveMarking).not.toHaveBeenCalled();
      expect(store.state.block).toMatch(/ИНН это 10 или 12 цифр/);

      // Try with wrong digit count
      store.set({ inn: "12345" });
      store.next();
      expect(saveMarking).not.toHaveBeenCalled();
      expect(store.state.block).toMatch(/ИНН это 10 или 12 цифр/);

      // Valid INN but empty name
      store.set({ inn: "7701234567", advertiser: { kind: "legal", name: "   ", phone: "", okved: "96.02" } });
      store.next();
      expect(saveMarking).not.toHaveBeenCalled();
      expect(store.state.block).toMatch(/Укажите наименование/);
    });
  });

  describe("BUG-019: Wizard draft persistence across reload", () => {
    it("19.1: saves and restores step and draftOff in draft storage", () => {
      saveDraft({
        site: "example.com",
        facts: [{ id: "f1", label: "Услуга", value: "Стрижка", source: "от вас" }],
        budget: 25000,
        step: 3,
        draftOff: { a1: true, a2: false },
      });
      const restored = loadDraft();
      expect(restored).not.toBeNull();
      expect(restored?.step).toBe(3);
      expect(restored?.draftOff).toEqual({ a1: true, a2: false });
      expect(restored?.budget).toBe(25000);
    });
  });

  describe("BUG-024: Site input placeholder", () => {
    it("24.1: placeholder indicates an example rather than looking like an entered value", () => {
      const store = new MockStore(initialState("start"));
      render(<Start store={store} />);
      const input = screen.getByLabelText(/Ссылка на сайт или страницу/i);
      expect(input).toHaveAttribute("placeholder", "Например: ваш-салон.рф");
    });
  });

  describe("UX: Main panel site creation", () => {
    it("renders '+ Новый сайт' button in header and opens CreateSiteModal", async () => {
      const store = new MockStore(initialState("work"));
      store.set({ live: { name: "Салон Пермь", budget: 20000, spent: 5000, hasCampaigns: true, alerts: [] } as any });
      render(<AppShell store={store} />);
      const btn = screen.getByRole("button", { name: "Создать новый сайт" });
      expect(btn).toBeInTheDocument();
      fireEvent.click(btn);
      expect(screen.getByRole("dialog", { name: "Добавить сайт или бизнес" })).toBeInTheDocument();
    });
  });

  describe("BUG-041: мастер нового сайта не тянет чужие объявления", () => {
    it("41.1: без написанных объявлений в сводке «пока нет»", () => {
      signedWizard(0);
      expect(screen.getByText("Объявления").parentElement).toHaveTextContent("пока нет");
    });

    it("41.2: правка профиля сбрасывает объявления, написанные по старым фактам", () => {
      const { store } = signedWizard(1);
      store.set({ ads: [{ id: "w0", name: "Стрижка", tag: "", tagTone: "accent", leads: 0, cost: 0, hook: "", title: "Стрижка", text: "", facts: "" }], adSet: { id: 1, token: "t", template: false, needsReview: false, facts: "{}" } });
      store.removeFact("s1");
      expect(store.state.ads).toEqual([]);
      expect(store.state.adSet).toBeNull();
    });
  });

  describe("BUG-043: при исчерпанном лимите шаг «Кабинет» не тупик", () => {
    it("43.1: без объявлений «Продолжить» не требует оставить объявление", async () => {
      const { store, user } = signedWizard(4);
      await user.click(screen.getByRole("button", { name: "Продолжить" }));
      await waitFor(() => expect(store.wizard).not.toBeNull());
      expect(store.state.block).not.toContain("Оставьте хотя бы одно объявление");
    });
  });

  describe("BUG-044 и BUG-042: вошедший владелец сохраняет мастер как новый сайт", () => {
    it("44.1: «Продолжить» на шаге «Кабинет» создаёт бизнес с профилем и бюджетом, а не выбрасывает черновик", async () => {
      const { store, deps, business, user } = signedWizard(4);
      expect(screen.getByText("Вход выполнен")).toBeInTheDocument();
      expect(screen.queryByText("Вход через Яндекс")).toBeNull();
      expect(screen.queryByText(/Профиль и бюджет сохранены/)).toBeNull();
      await user.click(screen.getByRole("button", { name: "Продолжить" }));
      await waitFor(() => expect(deps.createBusiness).toHaveBeenCalledTimes(1));
      expect(business.saveProfile).toHaveBeenCalled();
      expect(business.saveBudgetPlan).toHaveBeenCalledWith(20000);
      expect(store.navigate).not.toHaveBeenCalledWith("/");
      await waitFor(() => expect(deps.open).toHaveBeenCalledWith("new", "c"));
    });

    it("44.2: из мастера есть выход в кабинет, если сайты уже есть", async () => {
      clearDraft();
      const { AppShell } = await import("./AppShell");
      const store = new MockStore({ ...initialState("start"), ads: [], account: { email: "anna@example.ru" }, businesses: [{ id: "old", name: "Бигуди Пермь" }] });
      store.navigate = vi.fn();
      render(<AppShell store={store} />);
      await userEvent.click(screen.getByRole("button", { name: "В кабинет" }));
      expect(store.navigate).toHaveBeenCalledWith("/");
    });
  });
});
