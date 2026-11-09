import type { Api, Display } from "../api";
import { MockStore } from "../mock/MockStore";
import { AppShell } from "./AppShell";
import { draftProfile } from "./draft";
import { LiveSettings, servicesProblem } from "./screens/LiveSettings";
import { initialState } from "./store";
import { display, fakeApi, open, overview, settled } from "./testLive";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

describe("Настройки кабинета: профиль, бюджет, показы: регрессии (BUG-…)", () => {
  describe("BUG-016: LiveSettings.tsx Photos consent is reset after photo upload", () => {
    it("16.1: consent resets to false after upload", async () => {
      const api = fakeApi({
        photos: vi.fn(async () => ({ photos: [], max: 5 })),
        uploadPhoto: vi.fn(async () => ({ id: 1, url: "/photo1.jpg" })),
      } as unknown as Partial<Api>);
      const store = await open(api);
      render(<LiveSettings store={store} />);

      const consentBox = await screen.findByRole("checkbox", { name: /У меня есть права использовать фото/ });
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

  describe("BUG-025: Profile validation requires services and facts", () => {
    it("25.1: blocks proceeding when profile has no services", () => {
      const store = new MockStore(initialState("start"));
      store.set({
        step: 1,
        facts: [{ id: "f1", field: "address", label: "Адрес", value: "г. Москва", source: "от вас" }],
      });
      store.next();
      expect(store.state.block).toBe("Добавьте хотя бы одну услугу: объявления пишу по услугам.");
      expect(store.state.step).toBe(1);
    });
  });

  describe("BUG-027: Minimum budget explanation", () => {
    it("27.1: explains the 50 ₽/day + 10% reserve rule when below minimum", () => {
      const store = new MockStore(initialState("start"));
      store.set({ step: 2, budgetDraft: "500" });
      store.next();
      expect(store.state.block).toContain("от 50 ₽ в день по правилам Яндекса плюс 10% резерва");
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

  describe("BUG-046: цена услуги в настройках", () => {
    it("46.1: минус в цене не отбрасывается молча", async () => {
      const store = await open(fakeApi());
      render(<LiveSettings store={store} />);
      const price = await screen.findByLabelText("Цена услуги 1, ₽");
      await userEvent.clear(price);
      await userEvent.type(price, "-5");
      expect(screen.getByText("Цена — положительное число, без минуса и букв.")).toBeInTheDocument();
      expect(price).toHaveValue("-5");
      await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
      expect(screen.getByText("Цена — положительное число, без минуса и букв.")).toBeInTheDocument();
    });

    it("46.2: нулевая цена и цена в названии не сохраняются", () => {
      expect(servicesProblem([{ name: "Стрижка", price: 0 }])).toContain("больше нуля");
      expect(servicesProblem([{ name: "Стрижка 500р", price: 5 }])).toContain("есть цена");
      expect(servicesProblem([{ name: "Маникюр 1 200 ₽", price: null }])).toContain("есть цена");
      expect(servicesProblem([{ name: "Маникюр 3D", price: 1200 }])).toBeNull();
    });

    it("46.3: сохранение с ценой в названии не уходит в сервис", async () => {
      const saveProfile = vi.fn(async (p: unknown) => p);
      const store = await open(fakeApi({ saveProfile: saveProfile as never }));
      render(<LiveSettings store={store} />);
      const name = await screen.findByLabelText("Услуга 1");
      await userEvent.clear(name);
      await userEvent.type(name, "Стрижка 500р");
      await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
      expect(screen.getByText(/В названии «Стрижка 500р» есть цена/)).toBeInTheDocument();
      expect(saveProfile).not.toHaveBeenCalled();
    });
  });

  describe("BUG-048: смена сайта перезагружает форму настроек", () => {
    it("48.1: после переключения форма профиля из нового сайта", async () => {
      const store = await open(fakeApi({ profile: vi.fn(async () => ({ services: [{ name: "Стрижка 500р", price: 5 }], address: "", features: [], niche: "", website: "" })) }));
      render(<LiveSettings store={store} />);
      expect(await screen.findByDisplayValue("Стрижка 500р")).toBeInTheDocument();
      const other = fakeApi({ profile: vi.fn(async () => ({ services: [{ name: "Маникюр", price: 1200 }], address: "Пермь", features: [], niche: "", website: "https://salebot.site/" })) });
      await act(async () => {
        store.api = other;
        store.set({ activeBusinessId: "b2" });
      });
      expect(await screen.findByDisplayValue("Маникюр")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("Стрижка 500р")).toBeNull();
      expect(screen.getByLabelText("Адрес сайта, откуда взять фото")).toHaveValue("https://salebot.site/");
    });
  });

  describe("BUG-051: отказ в загрузке фото называет причину", () => {
    it("51.1: вторая попытка без согласия и не картинка дают причину и убирают «Фото добавил»", async () => {
      const uploadPhoto = vi.fn(async () => ({ id: 1, url: "/p/1" }));
      const store = await open(fakeApi({ uploadPhoto: uploadPhoto as never }));
      render(<LiveSettings store={store} />);
      await userEvent.click(await screen.findByLabelText(/У меня есть права использовать фото/));
      const field = screen.getByLabelText("Файл фото");
      await userEvent.upload(field, new File(["x"], "a.png", { type: "image/png" }));
      expect(await screen.findByText(/Фото добавил/)).toBeInTheDocument();

      await userEvent.upload(field, new File(["x"], "b.png", { type: "image/png" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Сначала отметьте");
      expect(screen.queryByText(/Фото добавил/)).toBeNull();

      await userEvent.click(screen.getByLabelText(/У меня есть права использовать фото/));
      await userEvent.upload(field, new File(["x"], "note.txt", { type: "text/plain" }), { applyAccept: false });
      expect(await screen.findByRole("alert")).toHaveTextContent("JPEG, PNG, GIF или WebP");
      expect(uploadPhoto).toHaveBeenCalledTimes(1);
    });
  });

  describe("BUG-048: строка сайта переключает с первого клика", () => {
    it("48.2: строки меню названы по сайту и переключают одним нажатием", async () => {
      const store = await open(fakeApi());
      store.set({ businesses: [{ id: "b1", name: "Салон Ромашка" }, { id: "b2", name: "Бигуди Пермь" }], activeBusinessId: "b1" });
      const switchBusiness = vi.spyOn(store, "switchBusiness").mockResolvedValue();
      render(<AppShell store={store} />);
      await userEvent.click(screen.getByRole("button", { name: /Выбрать бизнес/ }));
      expect(screen.getByRole("menuitemradio", { name: "Салон Ромашка" })).toHaveAttribute("aria-checked", "true");
      await userEvent.click(screen.getByRole("menuitemradio", { name: "Бигуди Пермь" }));
      expect(switchBusiness).toHaveBeenCalledOnce();
      expect(switchBusiness).toHaveBeenCalledWith("b2");
      expect(screen.queryByRole("menu")).toBeNull();
    });
  });

  describe("BUG-050: кнопки настроек объясняют отказ", () => {
    it("50.1: цена обращения 0 получает причину", async () => {
      const store = await open(fakeApi());
      render(<LiveSettings store={store} />);
      await userEvent.type(await screen.findByLabelText("Желаемая цена обращения, ₽"), "0");
      expect(screen.getByText("Цена должна быть больше нуля.")).toBeInTheDocument();
    });

    it("50.2: без кампаний нет подписи о «разном регионе», общий бюджет не придуман", async () => {
      localStorage.removeItem("adhelper.shared_budget");
      const store = await open(fakeApi({ display: vi.fn(async () => ({ ...display(), campaigns: 0, regions: null, regions_text: null, schedule: null, schedule_text: null })), budgetPlan: vi.fn(async () => ({ rubles: null })), overview: vi.fn(async () => ({ ...overview(), money: [], campaigns: [] })) }));
      render(<LiveSettings store={store} />);
      expect(await screen.findByText(/Кампаний ещё нет/)).toBeInTheDocument();
      expect(screen.queryByText(/разный регион/)).toBeNull();
      await userEvent.click(await screen.findByRole("button", { name: "Общий на все бизнесы" }));
      expect(screen.getByLabelText("Общий бюджет на все бизнесы, ₽ в месяц")).toHaveValue("");
      expect(screen.queryByText(/30.000/)).toBeNull();
    });
  });
});
