import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { formatErrorDetail } from "../api";
import type { Api, Display } from "../api";
import { History } from "./screens/History";
import { LiveSettings } from "./screens/LiveSettings";
import { AdDetail } from "./screens/AdDetail";
import { initialState} from "./store";
import { MockStore } from "../mock/MockStore";
import { fakeApi, open } from "./testLive";

describe("Review 2 Reproduction & Fix Verification Tests (BUG-032 – BUG-036)", () => {
  describe("BUG-032: History undo race condition — button disappears and state shows 'Отменяю…'", () => {
    it("32.1: does not show 'Отменить' button while rollback API call is in flight", async () => {
      let resolveRollback: (value: { status: "applied" }) => void;
      const rollbackPromise = new Promise<{ status: "applied" }>((resolve) => {
        resolveRollback = resolve;
      });

      const api = fakeApi({
        rollbackPreview: vi.fn(async () => ({
          possible: true,
          status: "ready",
          directions: 1,
          text: "Вернётся прежний лимит.",
        })),
        rollback: vi.fn(() => rollbackPromise),
      } as unknown as Partial<Api>);

      const store = await open(api);
      render(<History store={store} />);

      // Initially, "Отменить" button is visible
      const undoBtn = await screen.findByRole("button", { name: "Отменить" });
      await userEvent.click(undoBtn);

      // Confirm button appears
      const confirmBtn = await screen.findByRole("button", { name: "Да, отменить" });
      await userEvent.click(confirmBtn);

      // While rollback is in flight:
      // "Отменить" must NOT reappear, and status should show "Отменяю…"
      expect(screen.queryByRole("button", { name: "Отменить" })).not.toBeInTheDocument();
      expect(screen.getByText("Отменяю…")).toBeInTheDocument();

      // Resolve the rollback
      resolveRollback!({ status: "applied" });
      await waitFor(() => {
        expect(screen.getByText("Отменено вами")).toBeInTheDocument();
      });
    });
  });

  describe("BUG-033: formatErrorDetail formats FastAPI/Pydantic validation errors", () => {
    it("33.1: extracts message from Pydantic array of validation errors", () => {
      const pydanticError = [
        { loc: ["body", "inn"], msg: "ИНН должен содержать 10 или 12 цифр", type: "value_error" },
      ];
      expect(formatErrorDetail(pydanticError)).toBe("ИНН должен содержать 10 или 12 цифр");
    });

    it("33.2: joins multiple error messages", () => {
      const pydanticErrors = [
        { loc: ["body", "inn"], msg: "Неверный ИНН" },
        { loc: ["body", "name"], msg: "Укажите имя" },
      ];
      expect(formatErrorDetail(pydanticErrors)).toBe("Неверный ИНН; Укажите имя");
    });

    it("33.3: leaves plain string message untouched", () => {
      expect(formatErrorDetail("Сайт недоступен")).toBe("Сайт недоступен");
    });
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

  describe("BUG-035: LiveSettings sameRegions comparison handles unsorted array from API", () => {
    it("35.1: disables 'Изменить регион' button when regions match regardless of order", async () => {
      const displayData: Display = {
        campaigns: 1,
        schedule: { days: [0, 1, 2, 3, 4, 5, 6], start: 0, end: 24 },
        schedule_text: "круглосуточно",
        regions: [213, 1], // Unsorted order from backend
        regions_text: "Москва, Москва и область",
        goal: null,
        catalog: [
          { id: 1, name: "Москва и область" },
          { id: 213, name: "Москва" },
        ],
        min_weekly_hours: 40,
      };

      const api = fakeApi({
        display: vi.fn(async () => displayData),
      } as unknown as Partial<Api>);

      const store = await open(api);
      render(<LiveSettings store={store} />);

      const btn = await screen.findByRole("button", { name: "Изменить регион" });
      // When backend returned [213, 1] and store selected [213, 1], button should be disabled (no change)
      expect(btn).toBeDisabled();
    });
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
});
