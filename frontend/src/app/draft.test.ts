import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { Fact } from "./data";
import { businessName, clearDraft, clearStartReading, clearStartSite, clearWizard, draftProfile, loadDraft, loadStartReading, loadStartSite, loadWizard, saveDraft, saveStartReading, saveStartSite, saveWizard, transferDraft } from "./draft";
import type { StartDraft, TransferDeps } from "./draft";
import { siteFacts } from "./store";

const facts: Fact[] = [
  { id: "site0", field: "niche", label: "Чем занимаетесь", value: "маникюр", source: "с сайта", quote: "Студия маникюра" },
  { id: "site1", field: "address", label: "Где", value: "Казань, Даурская, 44Г", source: "с сайта", quote: "Казань, Даурская, 44Г" },
  { id: "site2", field: "service", label: "Услуга", value: "Маникюр с покрытием, 1 500 ₽", source: "с сайта", quote: "Маникюр с покрытием — 1 500 ₽" },
  { id: "site3", field: "service", label: "Услуга", value: "Педикюр", source: "от вас" },
  { id: "site4", field: "feature", label: "Особенность", value: "Парковка", source: "с сайта", quote: "Парковка" },
  { id: "n1", label: "Вы добавили", value: "Работаем без выходных", source: "от вас" },
];
const draft: StartDraft = { site: "https://nogti.ru/", facts, budget: 20000 };

function deps(over: Partial<TransferDeps> = {}) {
  const saved = { profile: vi.fn(async (p: unknown) => p), plan: vi.fn(async (n: number) => ({ rubles: n })) };
  const full: TransferDeps = {
    session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [] })),
    createBusiness: vi.fn(async (name: string) => ({ id: "b1", name })),
    business: vi.fn(() => ({ saveProfile: saved.profile, saveBudgetPlan: saved.plan })),
    ...over,
  };
  return { full, saved };
}

describe("черновик запуска", () => {
  it("свои выключенные объявления переносятся без включения, а правки не меняют выбор", async () => {
    const createWizardAd = vi.fn().mockResolvedValue({ id: "w1" });
    const updateWizardAd = vi.fn().mockResolvedValue({ id: "w0" });
    const { full } = deps({ business: () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), createWizardAd, updateWizardAd }) });
    await transferDraft(full, {
      ...draft,
      draftOff: { "custom-1": true, w0: true },
      customAds: [
        { id: "custom-1", headline: "Своё объявление", text: "Запишитесь", facts: [] },
        { id: "w0", headline: "Исправленный заголовок", text: "Ваш маникюр", facts: [] },
      ],
    });
    expect(createWizardAd).toHaveBeenCalledWith({ headline: "Своё объявление", text: "Запишитесь", client_id: "custom-1", active: false });
    expect(updateWizardAd).toHaveBeenCalledWith("w0", { headline: "Исправленный заголовок", text: "Ваш маникюр" });
  });

  it("становится профилем: цена отделяется от названия, слова владельца идут в особенности", () => {
    expect(draftProfile(draft)).toEqual({
      niche: "маникюр",
      address: "Казань, Даурская, 44Г",
      services: [{ name: "Маникюр с покрытием", price: 1500 }, { name: "Педикюр", price: null }],
      features: ["Парковка", "Работаем без выходных"],
      website: "https://nogti.ru/",
    });
  });

  it("факты разбора сайта попадают в свои поля профиля, а не в особенности", () => {
    const read = siteFacts([
      { field: "niche", value: "маникюр", price: null, quote: "Студия маникюра", page: "/" },
      { field: "service", value: "Маникюр с покрытием", price: 1500, quote: "Маникюр с покрытием — 1 500 ₽", page: "/" },
      { field: "address", value: "Казань, Даурская, 44Г", price: null, quote: "Казань, Даурская, 44Г", page: "/" },
    ]);
    expect(draftProfile({ site: "", facts: read, budget: 0 })).toMatchObject({
      niche: "маникюр",
      address: "Казань, Даурская, 44Г",
      services: [{ name: "Маникюр с покрытием", price: 1500 }],
      features: [],
    });
  });

  it("имя бизнеса берётся с адреса сайта, без сайта из вида деятельности", () => {
    expect(businessName(draft)).toBe("nogti.ru");
    expect(businessName({ ...draft, site: "" })).toBe("Маникюр");
    expect(
      businessName({
        ...draft,
        site: "",
        facts: [{ id: "f1", value: "Педикюр, 1 800 ₽", field: "service", label: "Услуга", source: "от вас" }],
      }),
    ).toBe("Педикюр");
    expect(businessName({ ...draft, site: "", facts: [] })).toBe("Мой бизнес");
  });

  it("переживает уход на вход Яндекса и стирается", () => {
    saveDraft(draft);
    expect(loadDraft()).toEqual(draft);
    clearDraft();
    expect(loadDraft()).toBeNull();
  });

  it("после входа создаёт бизнес, сохраняет профиль и бюджет, потом стирается", async () => {
    saveDraft(draft);
    const { full, saved } = deps();
    expect(await transferDraft(full, draft)).toEqual({ status: "done", email: "anna@example.ru", businessId: "b1", csrf: "c" });
    // Мастер продолжит с этим бизнесом и после возврата с подключения кабинета.
    expect(loadWizard()).toBe("b1");
    expect(full.createBusiness).toHaveBeenCalledWith("nogti.ru", "c");
    expect(full.business).toHaveBeenCalledWith("b1", "c");
    expect(saved.profile).toHaveBeenCalledWith(draftProfile(draft));
    expect(saved.plan).toHaveBeenCalledWith(20000);
    expect(loadDraft()).toBeNull();
  });

  it("переносит выбранные объявления мастера; пропавший набор вход не ломает", async () => {
    const ads = { id: 7, token: "k", kept: [1] };
    const adoptAds = vi.fn(async () => ({ kept: 1 }));
    const { full } = deps({ business: vi.fn(() => ({ saveProfile: vi.fn(async () => ({})), saveBudgetPlan: vi.fn(async () => ({})), adoptAds })) });
    expect(await transferDraft(full, { ...draft, ads })).toMatchObject({ status: "done" });
    expect(adoptAds).toHaveBeenCalledWith(ads);

    const gone = vi.fn(async () => { throw new ApiError(404, "Объявления не найдены"); });
    const second = deps({ business: vi.fn(() => ({ saveProfile: vi.fn(async () => ({})), saveBudgetPlan: vi.fn(async () => ({})), adoptAds: gone })) });
    expect(await transferDraft(second.full, { ...draft, ads })).toMatchObject({ status: "done" });
  });

  it("после отклонения набора переносит сохранённую правку как своё объявление", async () => {
    const input: StartDraft = {
      ...draft,
      ads: { id: 7, token: "k", kept: [0] },
      draftOff: { w0: true },
      customAds: [{ id: "w0", headline: "Мой заголовок", text: "Мой текст", facts: [] }],
    };
    const adoptAds = vi.fn().mockRejectedValue(new ApiError(409, "Набор устарел"));
    const createWizardAd = vi.fn().mockResolvedValue({ id: "w0" });
    const updateWizardAd = vi.fn();
    const business = () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), adoptAds, createWizardAd, updateWizardAd });
    saveDraft(input);

    await expect(transferDraft(deps({ business }).full, input)).resolves.toMatchObject({ status: "done" });

    expect(createWizardAd).toHaveBeenCalledWith({ headline: "Мой заголовок", text: "Мой текст", client_id: "transfer-w0", active: false });
    expect(updateWizardAd).not.toHaveBeenCalled();
    expect(loadDraft()).toBeNull();
  });

  it("повторяет fallback-создание после потерянного ответа тем же ключом", async () => {
    const input: StartDraft = {
      ...draft,
      ads: { id: 7, token: "k", kept: [0] },
      customAds: [{ id: "w0", headline: "Мой заголовок", text: "Мой текст", facts: [] }],
    };
    const adoptAds = vi.fn().mockRejectedValue(new ApiError(409, "Набор устарел"));
    const createWizardAd = vi.fn().mockRejectedValueOnce(new TypeError("Ответ потерялся")).mockResolvedValue({ id: "w1" });
    const updateWizardAd = vi.fn();
    const business = () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), adoptAds, createWizardAd, updateWizardAd });

    await expect(transferDraft(deps({ business }).full, input)).rejects.toThrow("Ответ потерялся");
    const kept = loadDraft();
    expect(kept).toMatchObject({ adoptionFailed: true, customAds: input.customAds });
    expect(kept?.ads).toBeUndefined();
    const retry = deps({ business, session: async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "b1", name: "nogti.ru" }] }) });
    await transferDraft(retry.full, kept!);

    expect(createWizardAd).toHaveBeenNthCalledWith(1, { headline: "Мой заголовок", text: "Мой текст", client_id: "transfer-w0" });
    expect(createWizardAd).toHaveBeenNthCalledWith(2, { headline: "Мой заголовок", text: "Мой текст", client_id: "transfer-w0" });
    expect(updateWizardAd).not.toHaveBeenCalled();
  });

  it("чужой бизнес у владельца не затирает", async () => {
    saveDraft(draft);
    const { full, saved } = deps({ session: vi.fn(async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "old", name: "Салон Анны" }] })) });
    expect(await transferDraft(full, draft)).toEqual({ status: "exists", name: "Салон Анны" });
    expect(full.createBusiness).not.toHaveBeenCalled();
    expect(saved.profile).not.toHaveBeenCalled();
    expect(loadDraft()).toBeNull();
  });

  it("при createAsNew создает новый бизнес для другого сайта", async () => {
    saveDraft({ ...draft, createAsNew: true });
    const { full, saved } = deps({ session: vi.fn(async () => ({ me: { email: "owner@test.ru", csrf_token: "c" }, businesses: [{ id: "old", name: "Салон Анны" }] })) });
    expect(await transferDraft(full, { ...draft, createAsNew: true })).toMatchObject({ status: "done", businessId: "b1" });
    expect(full.createBusiness).toHaveBeenCalledWith("nogti.ru", "c");
    expect(saved.profile).toHaveBeenCalled();
  });

  it("сбой посередине не создаёт второй бизнес при повторе", async () => {
    saveDraft(draft);
    const failing = vi.fn(async () => { throw new Error("Сервис не ответил"); });
    const first = deps({ business: vi.fn(() => ({ saveProfile: failing, saveBudgetPlan: failing })) });
    await expect(transferDraft(first.full, draft)).rejects.toThrow("Сервис не ответил");
    const kept = loadDraft();
    expect(kept?.businessId).toBe("b1");

    // Бизнес уже создан этим черновиком: повтор досохраняет в него же.
    const second = deps({ session: vi.fn(async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "b1", name: "nogti.ru" }] })) });
    expect(await transferDraft(second.full, kept!)).toMatchObject({ status: "done", businessId: "b1" });
    expect(second.full.createBusiness).not.toHaveBeenCalled();
  });

  it.each(["w0", "draft-0", "custom-1"])("ошибка переноса %s сохраняет объявление для повтора", async (id) => {
    const input: StartDraft = { ...draft, customAds: [{ id, headline: "Ваш маникюр", text: "Запишитесь", facts: [] }] };
    saveDraft(input);
    const failing = vi.fn().mockRejectedValue(new ApiError(500, "Сервис не ответил"));
    const { full } = deps({ business: () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), createWizardAd: failing, updateWizardAd: failing }) });

    await expect(transferDraft(full, input)).rejects.toThrow("Сервис не ответил");
    expect(loadDraft()).toMatchObject({ businessId: "b1", customAds: input.customAds });
  });

  it("повтор после частичного переноса не принимает набор заново и не создаёт объявление дважды", async () => {
    const input: StartDraft = {
      ...draft,
      ads: { id: 7, token: "k", kept: [0] },
      customAds: [
        { id: "custom-1", headline: "Своё объявление", text: "Запишитесь", facts: [] },
        { id: "w0", headline: "Правка объявления", text: "Ваш маникюр", facts: [] },
      ],
    };
    saveDraft(input);
    const adoptAds = vi.fn().mockResolvedValue({ kept: 1 });
    const createWizardAd = vi.fn().mockResolvedValue({ id: "w1" });
    const updateWizardAd = vi.fn().mockRejectedValueOnce(new TypeError("Сеть недоступна")).mockResolvedValue({ id: "w0" });
    const business = () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), adoptAds, createWizardAd, updateWizardAd });
    const first = deps({ business });

    await expect(transferDraft(first.full, input)).rejects.toThrow("Сеть недоступна");
    const kept = loadDraft();
    expect(kept?.businessId).toBe("b1");
    expect(kept?.ads).toBeUndefined();
    expect(kept?.customAds).toEqual([input.customAds![1]]);

    const second = deps({ business, session: async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "b1", name: "nogti.ru" }] }) });
    expect(await transferDraft(second.full, kept!)).toMatchObject({ status: "done", businessId: "b1" });
    expect(second.full.createBusiness).not.toHaveBeenCalled();
    expect(adoptAds).toHaveBeenCalledTimes(1);
    expect(createWizardAd).toHaveBeenCalledTimes(1);
    expect(updateWizardAd).toHaveBeenCalledTimes(2);
    expect(loadDraft()).toBeNull();
  });

  it("повтор после потерянного ответа создания использует тот же ключ объявления", async () => {
    const input: StartDraft = { ...draft, customAds: [{ id: "custom-stable", headline: "Маникюр", text: "Запишитесь", facts: [] }] };
    saveDraft(input);
    const createWizardAd = vi.fn().mockRejectedValueOnce(new TypeError("Ответ потерялся")).mockResolvedValue({ id: "w0" });
    const business = () => ({ saveProfile: vi.fn(), saveBudgetPlan: vi.fn(), createWizardAd });
    await expect(transferDraft(deps({ business }).full, input)).rejects.toThrow("Ответ потерялся");
    const kept = loadDraft();
    expect(kept?.customAds).toEqual(input.customAds);
    const retry = deps({ business, session: async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "b1", name: "nogti.ru" }] }) });
    await transferDraft(retry.full, kept!);
    for (const call of createWizardAd.mock.calls) {
      expect(call[0]).toEqual({ headline: "Маникюр", text: "Запишитесь", client_id: "custom-stable" });
    }
    expect(createWizardAd).toHaveBeenCalledTimes(2);
    expect(loadDraft()).toBeNull();
  });

  it("без входа ничего не переносит", async () => {
    const { full } = deps({ session: vi.fn(async () => null) });
    expect(await transferDraft(full, draft)).toEqual({ status: "signed_out" });
  });

  it("при повреждённом JSON в хранилище loadDraft возвращает null", () => {
    window.localStorage.setItem("adhelper.startDraft", "invalid-json{{{");
    expect(loadDraft()).toBeNull();
    clearDraft();
  });

  it("профиль без услуг продвигает первый свободный факт в услугу", () => {
    const freeOnly: Fact[] = [
      { id: "f1", label: "Свободный факт", value: "Маникюр классический, 1 000 ₽", source: "от вас" },
      { id: "f2", label: "Ещё факт", value: "Уютная атмосфера", source: "от вас" },
    ];
    const profile = draftProfile({ site: "https://test.ru", facts: freeOnly, budget: 10000 });
    expect(profile.services).toEqual([{ name: "Маникюр классический", price: 1000 }]);
    expect(profile.features).toContain("Уютная атмосфера");
  });

  it("имя бизнеса при некорректном URL использует нишу", () => {
    const badUrlDraft: StartDraft = {
      site: "http://[invalid-url",
      facts: [{ id: "n", field: "niche", label: "Ниша", value: "косметология", source: "от вас" }],
      budget: 15000,
    };
    expect(businessName(badUrlDraft)).toBe("Косметология");
  });

  it("ошибка сервера 500 при переносе объявлений пробрасывается дальше", async () => {
    const ads = { id: 8, token: "tok", kept: [0] };
    const failingAdopt = vi.fn(async () => { throw new ApiError(500, "Внутренняя ошибка сервиса"); });
    const { full } = deps({ business: vi.fn(() => ({ saveProfile: vi.fn(async () => ({})), saveBudgetPlan: vi.fn(async () => ({})), adoptAds: failingAdopt })) });
    await expect(transferDraft(full, { ...draft, ads })).rejects.toThrow("Внутренняя ошибка сервиса");
  });

  it("saveWizard, loadWizard и clearWizard сохраняют и удаляют ID бизнеса мастера", () => {
    saveWizard("biz-123");
    expect(loadWizard()).toBe("biz-123");
    clearWizard();
    expect(loadWizard()).toBeNull();
  });

  it("сохраняет и очищает введенный сайт и текущий разбор для восстановления при перезагрузке", () => {
    saveStartSite("salebot.site/st/bigoodi_perm");
    expect(loadStartSite()).toBe("salebot.site/st/bigoodi_perm");

    saveStartReading({ id: 12, token: "tok12", url: "https://salebot.site/st/bigoodi_perm" });
    expect(loadStartReading()).toEqual({ id: 12, token: "tok12", url: "https://salebot.site/st/bigoodi_perm" });

    clearStartReading();
    expect(loadStartReading()).toBeNull();
    expect(loadStartSite()).toBe("salebot.site/st/bigoodi_perm");

    clearStartSite();
    expect(loadStartSite()).toBe("");

    saveStartSite("site.ru");
    saveStartReading({ id: 13, token: "tok13", url: "https://site.ru" });
    clearDraft();
    expect(loadStartSite()).toBe("");
    expect(loadStartReading()).toBeNull();
  });
});
