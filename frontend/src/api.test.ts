import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi, createBusiness, createGuestApi, openSession } from "./api";

describe("api client", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("ApiError сохраняет статус и сообщение", () => {
    const err = new ApiError(404, "Не найдено");
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Не найдено");
  });

  it("успешные ответы: 200 с JSON и 204 No Content", async () => {
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, data: 123 }),
    } as Response);

    const guest = createGuestApi();
    const res = await guest.budgetOptions();
    expect(res).toEqual({ ok: true, data: 123 });
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/budget-plan/options", expect.objectContaining({
      credentials: "same-origin",
      headers: expect.objectContaining({ Accept: "application/json" }),
    }));

    // 204 No Content
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 204,
    } as Response);
    const api = createApi("b1", "csrf-token");
    const noContent = await api.logout();
    expect(noContent).toBeUndefined();
  });

  it("ошибки API: парсинг detail из JSON и дефолтный текст без тела", async () => {
    // С JSON detail
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ detail: "Неверный формат даты" }),
    } as Response);

    const guest = createGuestApi();
    await expect(guest.readSite("https://example.com")).rejects.toThrow(ApiError);

    // Без тела / html ошибка
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => { throw new Error("not json"); },
    } as unknown as Response);

    const api = createApi("b1", "csrf-token");
    await expect(api.history()).rejects.toThrow("Сервис не ответил");
  });

  it("проверяет методы createGuestApi и createBusiness", async () => {
    const mockJson = vi.fn().mockResolvedValue({ status: "ok" });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: mockJson,
    } as unknown as Response);

    const guest = createGuestApi();
    await guest.readSite("https://site.ru");
    await guest.siteRead(1, "tok");
    await guest.writeAds({ niche: "n", address: "a", services: [], features: [] });
    await guest.adSet(2, "tok2");

    await createBusiness("Мой бизнес", "csrf-123");
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/businesses", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-123" }),
    }));
  });

  it("проверяет методы createApi с параметрами и Range", async () => {
    const mockJson = vi.fn().mockResolvedValue({ status: "ok" });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: mockJson,
    } as unknown as Response);

    const api = createApi("b-test", "csrf-secret");

    // Range фильтры
    await api.overview({ since: "2026-11-01", until: "2026-11-05" });
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/businesses/b-test/overview?since=2026-11-01&until=2026-11-05", expect.anything());

    await api.overview({ since: "2026-11-01" }); // без until (до текущей даты площадки)
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/businesses/b-test/overview?since=2026-11-01", expect.anything());

    await api.overview(); // без Range
    await api.today();
    await api.answerTest(1, true);
    await api.cancelTest(2);
    await api.ads({ since: "2026-11-01", until: "2026-11-05" });
    await api.campaign("acc", "cmp1", { since: "2026-11-01", until: "2026-11-05" });
    await api.ad("acc", "ad1", { since: "2026-11-01", until: "2026-11-05" });
    await api.saveProfile({ niche: "n", address: "a", services: [], features: [], website: "" });
    await api.creatives();
    await api.launch(5);
    await api.rework("acc", "ad1", "сделай короче");
    await api.reworkSet(6);
    await api.chooseRework(6, 10);
    await api.withdraw(7);
    await api.accounts();
    await api.photos();
    expect(api.photoUrl(12)).toBe("/api/businesses/b-test/photos/12");

    // uploadPhoto и archivePhoto
    const blob = new Blob(["test"], { type: "image/png" });
    await api.uploadPhoto(blob, true);
    await api.archivePhoto(12);

    // connect с then и без
    await api.connect("start");
    await api.connect();

    // marking
    await api.marking();
    await api.saveMarking({ kind: "ip", inn: "123456789012", name: "ИП Иванов" });

    // tagCheck и launch
    await api.tagCheck();
    await api.requestTagCheck();
    await api.launchCheck();
    await api.startLaunch();
    await api.adoptAds({ id: 1, token: "tok", kept: [1] });
    await api.wizardAds();

    // каналы
    await api.channels();
    await api.linkTelegram();
    await api.unlinkTelegram();

    // управление объявлениями и бюджетом
    await api.setAdState("acc", "ad1", true, "req-1");
    await api.pause("req-2");
    await api.resume("req-3");
    await api.setBudget(35000, "req-4");
    await api.display();
    await api.command("поставь на паузу");
    await api.setSchedule([0, 1], 9, 18, "req-5");
    await api.skipDays([6], "req-6");
    await api.setRegions([1, 2], "req-7");
    await api.readSite("https://site.ru");
    await api.siteRead();
    await api.budgetPlan();
    await api.saveBudgetPlan(40000);
    await api.setGoal(750);
    await api.answerClients({ since: "2026-11-01", until: "2026-11-05" }, 5);
    await api.rollbackPreview(100);
    await api.rollback(100, true);
    await api.confirmBalance("acc");
  });

  it("openSession возвращает данные при успехе и null при ошибке", async () => {
    // Успех
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ email: "u@t.ru", csrf_token: "c" }) } as Response)
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => [{ id: "b1", name: "Бизнес" }] } as Response);

    const session = await openSession();
    expect(session).toEqual({
      me: { email: "u@t.ru", csrf_token: "c" },
      businesses: [{ id: "b1", name: "Бизнес" }],
    });

    // Ошибка
    globalThis.fetch = vi.fn().mockRejectedValueOnce(new Error("Network failed"));
    const failedSession = await openSession();
    expect(failedSession).toBeNull();
  });
});
