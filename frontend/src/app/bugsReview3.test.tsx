import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { STEPS } from "./data";
import { draftProfile, loadDraft, saveDraft } from "./draft";
import { initialState, Store } from "./store";
import { Start } from "./screens/Start";
import { DayChart } from "./screens/overview/DayChart";

describe("Review 3 Reproduction & Fix Verification Tests (BUG-019 – BUG-031, BUG-038)", () => {
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

  describe("BUG-020: Chat budget command in start stage", () => {
    it("20.1: updates budget and provides informative refusal for unsupported schedule/pause commands", () => {
      const store = new Store(initialState("start"));
      store.set({ step: 4 });
      store.send("Поменяй бюджет на 30 000 и поставь паузу на выходные");
      expect(store.state.f.budget).toBe(30000);
      const lastReply = store.state.msgs.at(-1)?.text;
      expect(lastReply).not.toBe("Понял. Когда будете готовы, напишите «дальше» или нажмите кнопку на вкладке «Настройка».");
      expect(lastReply).toContain("Расписание и паузы смогу настроить после запуска рекламы");
    });
  });

  describe("BUG-021: Hints do not hallucinate fake sums and districts", () => {
    it("21.1: STEPS does not hardcode 20 000 ₽ or hallucinated districts", () => {
      expect(STEPS[2]?.ai).not.toContain("20 000 ₽");
      expect(STEPS[2]?.ai).not.toContain("вашем районе");
    });
  });

  describe("BUG-022: Sidebar ads count retained after navigating back", () => {
    it("22.1: sidebar shows ads count when ads exist even after stepping back", () => {
      const store = new Store(initialState("start"));
      store.set({
        site: "salon.ru",
        step: 3,
        ads: [{ id: "a1", name: "Реклама 1", title: "Заголовок 1", hook: "Крючок", text: "Текст", facts: "Факты" }],
        draftOff: {},
      });
      // Step back to step 2
      store.set({ step: 2 });
      render(<Start store={store} />);
      const side = screen.getByRole("complementary", { name: "Что уже готово" });
      expect(side).toHaveTextContent("1 готово");
    });
  });

  describe("BUG-024: Site input placeholder", () => {
    it("24.1: placeholder indicates an example rather than looking like an entered value", () => {
      const store = new Store(initialState("start"));
      render(<Start store={store} />);
      const input = screen.getByLabelText(/Ссылка на сайт или страницу/i);
      expect(input).toHaveAttribute("placeholder", "Например: ваш-салон.рф");
    });
  });

  describe("BUG-025: Profile validation requires services and facts", () => {
    it("25.1: blocks proceeding when profile has no services", () => {
      const store = new Store(initialState("start"));
      store.set({
        step: 1,
        facts: [{ id: "f1", field: "address", label: "Адрес", value: "г. Москва", source: "от вас" }],
      });
      store.next();
      expect(store.state.block).toBe("Добавьте хотя бы одну услугу: объявления пишу по услугам.");
      expect(store.state.step).toBe(1);
    });
  });

  describe("BUG-026: Ad cards visual distinction", () => {
    it("26.1: renders explicit status badges for kept and dropped ads", () => {
      const store = new Store(initialState("start"));
      store.set({
        step: 3,
        ads: [
          { id: "a1", name: "Объявление 1", title: "Объявление 1", hook: "Крючок", text: "Текст 1", facts: "Факты" },
          { id: "a2", name: "Объявление 2", title: "Объявление 2", hook: "Крючок", text: "Текст 2", facts: "Факты" },
        ],
        draftOff: { a2: true },
      });
      render(<Start store={store} />);
      expect(screen.getByText("В рекламу")).toBeInTheDocument();
      expect(screen.getByText("Убрано")).toBeInTheDocument();
    });
  });

  describe("BUG-027: Minimum budget explanation", () => {
    it("27.1: explains the 50 ₽/day + 10% reserve rule when below minimum", () => {
      const store = new Store(initialState("start"));
      store.set({ step: 2, budgetDraft: "500" });
      store.next();
      expect(store.state.block).toContain("от 50 ₽ в день по правилам Яндекса плюс 10% резерва");
    });
  });

  describe("BUG-028: Ads limit recovery", () => {
    it("28.1: allows proceeding to cabinet step when ad generation limit is reached", () => {
      const store = new Store(initialState("start"));
      store.writer = {} as any;
      store.set({
        step: 3,
        ads: [],
        block: "Лимит на генерацию объявлений исчерпан. Попробуйте позже.",
      });
      store.next();
      expect(store.state.step).toBe(4);
    });
  });

  describe("BUG-029: DayChart with zero budget", () => {
    it("29.1: does not show fake 1000 ₽ scale and explains budget is not set", () => {
      const store = new Store(initialState());
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

  describe("BUG-030: Profile price parsing in draft", () => {
    it("30.1: parses price without comma and with different currency notations", () => {
      const profile = draftProfile({
        site: "test.ru",
        budget: 15000,
        facts: [
          { id: "f1", field: "service", label: "Услуга", value: "Стрижка 500р", source: "от вас" },
          { id: "f2", field: "service", label: "Услуга", value: "Маникюр 1200 руб", source: "от вас" },
          { id: "f3", field: "service", label: "Услуга", value: "Окрашивание - 3000 ₽", source: "от вас" },
        ],
      });
      expect(profile.services).toEqual([
        { name: "Стрижка", price: 500 },
        { name: "Маникюр", price: 1200 },
        { name: "Окрашивание", price: 3000 },
      ]);
    });
  });

  describe("BUG-038: Chat handles question about budget without mistaking for command", () => {
    it("38.1: answers question with current budget facts rather than proposing modification", () => {
      const store = new Store(initialState());
      store.set({ f: { ...store.state.f, budget: 15000 } });
      store.send("Какой у меня бюджет?");
      expect(store.state.pending).toBeNull();
      const lastReply = store.state.msgs.at(-1)?.text;
      expect(lastReply).toContain("15 000 ₽");
      expect(lastReply).toContain("Изменить бюджет можно");
    });
  });
});
