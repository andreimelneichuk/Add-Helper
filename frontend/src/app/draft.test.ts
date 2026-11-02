import { describe, expect, it, vi } from "vitest";
import type { Fact } from "./data";
import { businessName, clearDraft, draftProfile, loadDraft, loadWizard, saveDraft, transferDraft } from "./draft";
import type { StartDraft, TransferDeps } from "./draft";

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
  it("становится профилем: цена отделяется от названия, слова владельца идут в особенности", () => {
    expect(draftProfile(draft)).toEqual({
      niche: "маникюр",
      address: "Казань, Даурская, 44Г",
      services: [{ name: "Маникюр с покрытием", price: 1500 }, { name: "Педикюр", price: null }],
      features: ["Парковка", "Работаем без выходных"],
      website: "https://nogti.ru/",
    });
  });

  it("имя бизнеса берётся с адреса сайта, без сайта из вида деятельности", () => {
    expect(businessName(draft)).toBe("nogti.ru");
    expect(businessName({ ...draft, site: "" })).toBe("Маникюр");
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

  it("чужой бизнес у владельца не затирает", async () => {
    saveDraft(draft);
    const { full, saved } = deps({ session: vi.fn(async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "old", name: "Салон Анны" }] })) });
    expect(await transferDraft(full, draft)).toEqual({ status: "exists", name: "Салон Анны" });
    expect(full.createBusiness).not.toHaveBeenCalled();
    expect(saved.profile).not.toHaveBeenCalled();
    expect(loadDraft()).toBeNull();
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

  it("без входа ничего не переносит", async () => {
    const { full } = deps({ session: vi.fn(async () => null) });
    expect(await transferDraft(full, draft)).toEqual({ status: "signed_out" });
  });
});
