import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { AdSet, SiteRead } from "../../api";
import { clearDraft, clearWizard, loadDraft, loadWizard } from "../draft";
import type { TransferDeps } from "../draft";
import { initialState, Store } from "../store";
import { MockStore } from "../../mock/MockStore";
import type { GuestReader, GuestWriter, WizardApi } from "../store";
import { formatSite, Start } from "./Start";

function setup(patch: Parameters<Store["set"]>[0] = {}) {
  const store = new MockStore({ ...initialState("start"), ...patch });
  render(<Start store={store} />);
  return { store, user: userEvent.setup() };
}
const button = (name: string | RegExp) => screen.getByRole("button", { name });
const heading = (name: string) => screen.getByRole("heading", { level: 1, name });
const side = () => within(screen.getByRole("complementary", { name: "Что уже готово" }));
const launch = () => within(screen.getByLabelText("Итоговая сводка"));

describe("экран запуска", () => {
  it("можно задать свою сумму, меньше минимума дальше не пройти", async () => {
    const { store, user } = setup({ step: 2 });
    const field = screen.getByLabelText("Своя сумма в месяц, ₽");
    await user.type(field, "500");
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(2);
    expect(screen.getByRole("alert")).toHaveTextContent("от 1");
    await user.clear(field);
    await user.type(field, "32000");
    // Прогноз обращений не выдумываем: только сумма в день, её считает код.
    expect(screen.getByText(/около 1.067.₽ в день/)).toBeInTheDocument();
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(3);
    expect(store.state.f.budget).toBe(32000);
  });

  it("факт можно изменить прямо в карточке: источник меняется, Enter не уводит с шага", async () => {
    const { store, user } = setup({ step: 1 });
    await user.click(button("Изменить: Когда работаете"));
    const field = screen.getByLabelText("Когда работаете");
    await user.clear(field);
    await user.type(field, "Каждый день, 10:00–22:00{Enter}");
    expect(store.state.step).toBe(1);
    expect(screen.getByText("Каждый день, 10:00–22:00")).toBeInTheDocument();
    expect(store.state.facts.find((f) => f.id === "hours")).toMatchObject({ source: "от вас" });
  });

  it("с незавершённой правкой дальше не пройти, пустой факт не сохранить", async () => {
    const { store, user } = setup({ step: 1 });
    await user.click(button("Изменить: Акция"));
    await user.clear(screen.getByLabelText("Акция"));
    await user.click(button("Сохранить"));
    expect(screen.getByLabelText("Акция")).toBeInTheDocument();
    await user.click(button("Всё верно"));
    expect(store.state.step).toBe(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Сохраните");
  });

  it("неверный факт можно убрать, новый добавить", async () => {
    const { store, user } = setup({ step: 1 });
    await user.click(button("Изменить: Акция"));
    await user.click(button("Убрать"));
    expect(store.state.facts.some((f) => f.id === "promo")).toBe(false);
    await user.click(button("Добавить факт"));
    await user.type(screen.getByLabelText("Новый факт"), "Есть парковка");
    await user.click(button("Сохранить"));
    expect(screen.getByText("Есть парковка")).toBeInTheDocument();
  });

  it("пустая ссылка не пускает дальше и называет причину", async () => {
    const { store, user } = setup();
    await user.click(button("Изучить"));
    expect(store.state.step).toBe(0);
    expect(screen.getByRole("alert")).toHaveTextContent("Вставьте ссылку");

    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "   ");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(button("Изучить"));
    expect(store.state.step).toBe(0);
  });

  it("проходит от ссылки до запуска", async () => {
    const { store, user } = setup();
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "salon-primer.ru{Enter}");
    heading("Вот что я понял о вас");
    expect(side().getByText("salon-primer.ru")).toBeInTheDocument();

    await user.click(button("Всё верно"));
    heading("Сколько готовы тратить в месяц?");
    expect(document.activeElement).toBe(heading("Сколько готовы тратить в месяц?"));
    await user.click(button("Продолжить"));
    heading("Вот ваши объявления");
    await user.click(button("Нравится"));
    heading("Подключите кабинет Яндекса");
    await user.click(button("Подключено"));
    await user.click(button("Продолжить"));
    heading("Укажите ИНН для маркировки");
    await user.type(screen.getByLabelText("ИНН для маркировки"), "7701234567");
    await user.click(button("Продолжить"));
    heading("Поставьте код статистики на сайт");
    await user.click(button("Проверить установку"));
    expect(side().getByText("3 из 3")).toBeInTheDocument();
    await user.click(button("Продолжить"));
    heading("Всё готово к запуску");

    await user.click(button("Запустить рекламу"));
    expect(store.state.stage).toBe("work");
  });

  it("кнопка «Назад» возвращает на шаг и снимает причину блокировки", async () => {
    const { store, user } = setup({ step: 4, block: "Подключите кабинет Яндекса." });
    await user.click(button("Назад"));
    expect(store.state.step).toBe(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("каждое подключение это свой шаг, без него дальше не пройти", async () => {
    const { store, user } = setup({ step: 4 });
    const blocked = async (step: number, why: string) => {
      await user.click(button("Продолжить"));
      expect(store.state.step).toBe(step);
      expect(screen.getByRole("alert")).toHaveTextContent(why);
    };
    await blocked(4, "Подключите кабинет");
    await user.click(button("Подключено"));
    expect(screen.getByText(/логин salon-primer/)).toBeInTheDocument();
    await user.click(button("Продолжить"));

    await blocked(5, "10 или 12 цифр");
    const inn = screen.getByLabelText("ИНН для маркировки");
    expect(inn).toHaveAttribute("inputmode", "numeric");
    await user.type(inn, "77a01б");
    expect(inn).toHaveValue("7701");
    await blocked(5, "10 или 12 цифр");
    await user.type(inn, "234567");
    await user.click(button("Продолжить"));

    await blocked(6, "Проверьте установку");
    await user.click(button("Проверить установку"));
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(7);
  });

  it("письмо разработчику можно скопировать, его текст виден на экране", async () => {
    const { user } = setup({ step: 6 });
    await user.click(button("Скопировать письмо разработчику"));
    expect(button("Письмо скопировано")).toBeInTheDocument();
    expect(screen.getByText(/код статистики Яндекс Метрики/)).toBeInTheDocument();
  });

  it("в сводке запуска есть счёт", () => {
    setup({ step: 7 });
    expect(launch().getByText("На счёте в Яндексе")).toBeInTheDocument();
  });

  it("бюджет и снятое объявление попадают в итоговую сводку", async () => {
    const { store, user } = setup({ step: 2, site: "salon-primer.ru" });
    await user.click(button(/25.000/));
    expect(store.state.f.budget).toBe(25000);
    expect(button(/25.000/)).toHaveAttribute("aria-pressed", "true");
    expect(button(/20.000/)).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByText(/Примерно/)).not.toBeInTheDocument();
    expect(screen.getByText(/Прогноз обращений покажу/)).toBeInTheDocument();
    await user.click(button("Продолжить"));

    await user.click(button(/^Убрать «Маникюр от 1\s200\s₽»$/));
    await user.click(button("Убрать «Педикюр рядом с домом»"));
    await user.click(button("Вернуть «Педикюр рядом с домом»"));
    expect(store.state.draftOff).toEqual({ a2: true, a3: false });
    await user.click(button("Нравится"));

    act(() => store.set({ yandex: true, inn: "7701234567", metrica: true, step: 7 }));

    expect(launch().getByText(/25 000.*в месяц/)).toBeInTheDocument();
    expect(launch().getByText("2")).toBeInTheDocument();
    expect(side().getByText(/25 000.*в месяц/)).toBeInTheDocument();
    expect(side().getByText("2 готово")).toBeInTheDocument();
  });

  it("без объявлений дальше не пускает", async () => {
    const { store, user } = setup({ step: 3, draftOff: { a1: true, a2: true, a3: true } });
    await user.click(button("Нравится"));
    expect(store.state.step).toBe(3);
    expect(screen.getByRole("alert")).toHaveTextContent("Оставьте хотя бы одно объявление.");
    await user.click(button("Вернуть «Первый визит со скидкой»"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(button("Нравится"));
    expect(store.state.step).toBe(4);
  });

  it("чат и подсказка 'Можно спросить в чате' отключены на этапе онбординга", () => {
    setup({ step: 1 });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Можно спросить в чате" })).not.toBeInTheDocument();
  });
});

describe("разбор сайта до входа", () => {
  const ready: SiteRead = {
    id: 7, url: "https://nogti.ru/", status: "ready", problem: null, problem_text: null, pages: ["https://nogti.ru/"],
    facts: [
      { field: "niche", value: "маникюр", price: null, quote: "Студия маникюра «Ноготок»", page: "https://nogti.ru/" },
      { field: "service", value: "Маникюр с покрытием", price: 1500, quote: "Маникюр с покрытием — 1 500 ₽", page: "https://nogti.ru/price" },
      { field: "feature", value: "Оплата картой", price: null, quote: "Принимаем оплату картой", page: "https://nogti.ru/" },
    ],
  };
  const queued = { id: 7, token: "t".repeat(40), status: "queued", url: "https://nogti.ru/" };

  function withReader(reader: Partial<GuestReader>) {
    const full: GuestReader = {
      readSite: vi.fn(async () => queued),
      siteRead: vi.fn(async () => ready),
      ...reader,
    };
    const store = new MockStore(initialState("start"));
    store.reader = full;
    store.waitMs = 0;
    render(<Start store={store} />);
    return { store, reader: full, user: userEvent.setup() };
  }

  it("читает сайт и показывает найденные факты с цитатами", async () => {
    const answers = [{ ...ready, status: "running" as const, facts: [] }, ready];
    const { store, reader, user } = withReader({ siteRead: vi.fn(async () => answers.shift()!) });
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "nogti.ru");
    await user.click(button("Изучить"));
    expect(reader.readSite).toHaveBeenCalledWith("nogti.ru");
    await screen.findByRole("heading", { level: 1, name: "Вот что я понял о вас" });
    expect(reader.siteRead).toHaveBeenCalledWith(7, queued.token, { signal: expect.any(AbortSignal) });
    expect(screen.getByText(/^Маникюр с покрытием, 1.500.₽$/)).toBeInTheDocument();
    expect(screen.getByText("«Маникюр с покрытием — 1 500 ₽»")).toBeInTheDocument();
    expect(screen.getByText("Оплата картой")).toBeInTheDocument();
    // Подставных фактов прототипа здесь нет: только найденное на сайте.
    expect(screen.queryByText("Скидка 20% на первый визит")).not.toBeInTheDocument();
    expect(store.state.site).toBe("https://nogti.ru/");
  });

  it("пока сайт читается, кнопка «Изучить» отключена и второй разбор не ставится", async () => {
    const { reader, user } = withReader({ siteRead: vi.fn(() => new Promise<SiteRead>(() => undefined)) });
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "nogti.ru");
    await user.click(button("Изучить"));
    expect(await screen.findByRole("status")).toHaveTextContent("Читаю сайт");
    expect(button("Изучить")).toBeDisabled();
    await user.click(button("Изучить"));
    expect(reader.readSite).toHaveBeenCalledTimes(1);
  });

  it("в боковой панели сайт форматируется без протокола и имеет подсказку", async () => {
    const { user } = setup();
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "https://salebot.site/st/bigoodi_perm");
    const siteEl = side().getByText("salebot.site/st/bigoodi_perm");
    expect(siteEl).toBeInTheDocument();
    expect(siteEl).toHaveAttribute("title", "https://salebot.site/st/bigoodi_perm");
    expect(side().queryByText(/https:\/\//)).not.toBeInTheDocument();
  });

  it("правка факта с сайта снимает цитату: теперь это слова владельца", async () => {
    const { store, user } = withReader({});
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "nogti.ru{Enter}");
    await screen.findByRole("heading", { level: 1, name: "Вот что я понял о вас" });
    await user.click(button("Изменить: Чем занимаетесь"));
    await user.clear(screen.getByLabelText("Чем занимаетесь"));
    await user.type(screen.getByLabelText("Чем занимаетесь"), "маникюр и педикюр{Enter}");
    expect(store.state.facts.find((f) => f.label === "Чем занимаетесь")).toMatchObject({ source: "от вас", quote: undefined });
    expect(screen.queryByText("«Студия маникюра «Ноготок»»")).not.toBeInTheDocument();
  });

  it("сайт не прочитался: причина словами и путь вручную", async () => {
    const failed: SiteRead = { ...ready, status: "failed", problem: "empty_page", problem_text: "На странице почти нет текста", facts: [] };
    const { store, user } = withReader({ siteRead: vi.fn(async () => failed) });
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "nogti.ru{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("почти нет текста");
    expect(store.state.step).toBe(0);
    await user.click(button("Заполнить вручную"));
    heading("Вот что я понял о вас");
    expect(store.state.facts).toEqual([]);
    // Без единого факта рекламу писать не из чего.
    await user.click(button("Всё верно"));
    expect(store.state.step).toBe(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Добавьте хотя бы один факт");
  });

  it("отказ сервиса показывается как есть: лимит по адресу", async () => {
    const { store, user } = withReader({
      readSite: vi.fn(async () => { throw new ApiError(429, "С этого адреса сайт сегодня разбирали уже 3 раза"); }),
    });
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "nogti.ru{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("уже 3 раза");
    expect(store.state.step).toBe(0);
  });

  it("ссылку можно дать и в чате", async () => {
    const { store, reader } = withReader({});
    act(() => store.send("nogti.ru"));
    await waitFor(() => expect(store.state.step).toBe(1));
    expect(reader.readSite).toHaveBeenCalledWith("nogti.ru");
    expect(store.state.msgs.at(-1)?.text).toMatch(/Прочитал сайт/);
  });
});

describe("бюджет и вход в настоящем запуске", () => {
  const options = { min: 1550, max: 1_000_000, presets: [10000, 30000] };
  const facts = [{ id: "site0", field: "niche" as const, label: "Чем занимаетесь", value: "маникюр", source: "с сайта", quote: "Студия маникюра" }];

  function real(patch: Parameters<Store["set"]>[0] = {}) {
    clearDraft();
    const store = new MockStore({ ...initialState("start"), facts, site: "https://nogti.ru/", budgetOptions: options, ...patch });
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.navigate = vi.fn();
    render(<Start store={store} />);
    return { store, user: userEvent.setup() };
  }

  it("варианты и пределы бюджета берёт у сервиса", async () => {
    const { store, user } = real({ step: 2 });
    expect(button(/10.000/)).toBeInTheDocument();
    expect(button(/30.000/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /20.000/ })).not.toBeInTheDocument();
    const field = screen.getByLabelText("Своя сумма в месяц, ₽");
    await user.type(field, "1500");
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(2);
    expect(screen.getByRole("alert")).toHaveTextContent(/от 1.550/);
    await user.clear(field);
    await user.type(field, "2000000");
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(2);
    expect(screen.getByRole("alert")).toHaveTextContent("лишнего нуля");
  });

  it("вход уносит черновик с собой и возвращает в запуск", async () => {
    const { store, user } = real({ step: 4, f: { ...initialState().f, budget: 30000 } });
    await user.click(button("Продолжить"));
    expect(screen.getByRole("alert")).toHaveTextContent("Войдите через Яндекс");
    await user.click(button("Войти через Яндекс"));
    expect(store.navigate).toHaveBeenCalledWith("/api/auth/yandex/login?then=start");
    expect(loadDraft()).toMatchObject({ site: "https://nogti.ru/", budget: 30000, facts });
  });

  it("после входа переносит черновик и ведёт в кабинет", async () => {
    const { store, user } = real({ step: 0, facts: [], site: "" });
    const { saveDraft } = await import("../draft");
    saveDraft({ site: "https://nogti.ru/", facts, budget: 30000 });
    const saveProfile = vi.fn(async () => ({}));
    const saveBudgetPlan = vi.fn(async () => ({}));
    const deps: TransferDeps = {
      session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [] })),
      createBusiness: vi.fn(async (name: string) => ({ id: "b1", name })),
      business: vi.fn(() => ({ saveProfile, saveBudgetPlan })),
    };
    await act(() => store.resume(deps, null));
    expect(store.state.step).toBe(4);
    expect(saveBudgetPlan).toHaveBeenCalledWith(30000);
    expect(screen.getByText(/Вы вошли: anna@example.ru/)).toBeInTheDocument();
    expect(screen.getByText(/профиль и бюджет сохранены/i)).toBeInTheDocument();
    await user.click(button("Продолжить"));
    expect(store.navigate).toHaveBeenCalledWith("/");
  });

  it("у владельца уже есть бизнес: черновик его не затирает, это сказано", async () => {
    const { store } = real({ step: 0 });
    const { saveDraft } = await import("../draft");
    saveDraft({ site: "https://nogti.ru/", facts, budget: 30000 });
    const deps: TransferDeps = {
      session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [{ id: "old", name: "Салон Анны" }] })),
      createBusiness: vi.fn(),
      business: vi.fn(),
    };
    await act(() => store.resume(deps, null));
    expect(screen.getByRole("alert")).toHaveTextContent("«Салон Анны»");
    expect(deps.createBusiness).not.toHaveBeenCalled();
  });

  it("вход не состоялся: черновик на месте, причина словами", async () => {
    const { store } = real({ step: 0 });
    const { saveDraft } = await import("../draft");
    saveDraft({ site: "https://nogti.ru/", facts, budget: 30000 });
    const deps: TransferDeps = { session: vi.fn(async () => null), createBusiness: vi.fn(), business: vi.fn() };
    await act(() => store.resume(deps, "denied"));
    expect(store.state.step).toBe(4);
    expect(store.state.facts).toEqual(facts);
    expect(screen.getByRole("alert")).toHaveTextContent("Вход не состоялся");
    expect(loadDraft()).not.toBeNull();
  });
});

describe("повторный перенос", () => {
  it("второй вызов не создаёт второй бизнес", async () => {
    const { saveDraft } = await import("../draft");
    saveDraft({ site: "https://nogti.ru/", facts: [], budget: 20000 });
    const store = new MockStore(initialState("start"));
    const createBusiness = vi.fn(async (name: string) => ({ id: "b1", name }));
    const deps: TransferDeps = {
      session: vi.fn(async () => ({ me: { email: null, csrf_token: "c" }, businesses: [] })),
      createBusiness,
      business: vi.fn(() => ({ saveProfile: vi.fn(async () => ({})), saveBudgetPlan: vi.fn(async () => ({})) })),
    };
    await Promise.all([store.resume(deps, null), store.resume(deps, null)]);
    expect(createBusiness).toHaveBeenCalledTimes(1);
  });
});

describe("шаги после входа на сервисе", () => {
  const facts = [{ id: "site0", field: "niche" as const, label: "Чем занимаетесь", value: "маникюр", source: "с сайта", quote: "Студия маникюра" }];
  const found = { status: "found" as const, url: "https://nogti.ru/", counters: [98765432], problem: null };

  function wizardApi(over: Partial<WizardApi> = {}): WizardApi {
    return {
      accounts: vi.fn(async () => []),
      connect: vi.fn(async () => ({ authorize_url: "https://oauth.yandex.ru/authorize?x=1" })),
      marking: vi.fn(async () => ({ saved: null })),
      saveMarking: vi.fn(async (body) => ({ saved: { kind: body.kind, inn: "••••••3893", name: body.name, phone: null, okved: body.okved ?? null } })),
      tagCheck: vi.fn(async () => ({ check: null, site: "https://nogti.ru/" })),
      requestTagCheck: vi.fn(async () => ({ check: { ...found, status: "queued" as const, counters: [] }, site: "https://nogti.ru/" })),
      launchCheck: vi.fn(async () => ({ items: [], ready: false, launch_available: false, launch_text: "" })),
      manualTokenLink: vi.fn(async () => ({ url: "https://oauth.yandex.ru/authorize?manual=1" })),
      saveManualToken: vi.fn(async (_token: string, login?: string) => ({ id: "m1", platform: "yandex_direct", login: login || "anna-token", scopes: [], state: "connected" as const })),
      ...over,
    } as WizardApi;
  }

  function wizard(step: number, api: WizardApi, patch: Parameters<Store["set"]>[0] = {}) {
    clearDraft();
    const store = new MockStore({ ...initialState("start"), facts, site: "https://nogti.ru/", step, account: { email: "anna@example.ru" }, ...patch });
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.navigate = vi.fn();
    store.wizard = api;
    store.waitMs = 1;
    render(<Start store={store} />);
    return { store, user: userEvent.setup() };
  }

  it("после входа мастер идёт дальше с бизнесом, а не уходит в кабинет", async () => {
    const { saveDraft } = await import("../draft");
    clearDraft();
    saveDraft({ site: "https://nogti.ru/", facts, budget: 30000 });
    const store = new MockStore({ ...initialState("start") });
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.navigate = vi.fn();
    const api = wizardApi({ accounts: vi.fn(async () => [{ id: "a", platform: "yandex_direct", login: "anna", scopes: [], state: "connected" }]) });
    const open = vi.fn(() => api);
    render(<Start store={store} />);
    const deps = {
      session: vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [] })),
      createBusiness: vi.fn(async (name: string) => ({ id: "b1", name })),
      business: vi.fn(() => ({ saveProfile: vi.fn(async () => ({})), saveBudgetPlan: vi.fn(async () => ({})) })),
      open,
    };
    await act(() => store.resume(deps, null));
    expect(open).toHaveBeenCalledWith("b1", "c");
    expect(loadWizard()).toBe("b1");
    await waitFor(() => expect(screen.getByText("Кабинет подключён: anna")).toBeInTheDocument());
    await userEvent.click(button("Продолжить"));
    expect(store.state.step).toBe(5);
    expect(store.navigate).not.toHaveBeenCalled();
  });

  it("кабинет подключается через Яндекс с возвратом в мастер", async () => {
    const api = wizardApi();
    const { store, user } = wizard(4, api);
    await user.click(await screen.findByRole("button", { name: "Войти через Яндекс OAuth" }));
    expect(api.connect).toHaveBeenCalledWith("start");
    expect(store.navigate).toHaveBeenCalledWith("https://oauth.yandex.ru/authorize?x=1");
  });

  it("кабинет подключается через ручной токен доступа", async () => {
    const api = wizardApi();
    const { store, user } = wizard(4, api);
    const tokenInput = await screen.findByPlaceholderText("Вставьте токен доступа из адресной строки");
    await user.type(tokenInput, "y0_manual_token_secret");
    await user.click(screen.getByRole("button", { name: "Сохранить токен" }));
    expect(api.saveManualToken).toHaveBeenCalledWith("y0_manual_token_secret", undefined);
    expect(store.state.yandex).toBe(true);
    expect(await screen.findByText("Кабинет подключён: anna-token")).toBeInTheDocument();
  });

  it("на шаге 4 блокирует переход без подключения кабинета", async () => {
    const api = wizardApi();
    const { store, user } = wizard(4, api);
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(4);
    expect(await screen.findByRole("alert")).toHaveTextContent("Подключите кабинет Директа");
  });

  it("подключённый кабинет виден по логину и считается в подключениях", async () => {
    const api = wizardApi({ accounts: vi.fn(async () => [{ id: "a", platform: "yandex_direct", login: "anna-direct", scopes: [], state: "connected" }]) });
    wizard(4, api);
    expect(await screen.findByText("Кабинет подключён: anna-direct")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить токен" })).not.toBeInTheDocument();
    expect(side().getByText("1 из 3")).toBeInTheDocument();
  });

  it("отсутствие реквизитов маркировки в Директе держит на шаге и предлагает перейти в Яндекс", async () => {
    const api = wizardApi({ marking: vi.fn(async () => ({ saved: null })) });
    const { store, user } = wizard(5, api);
    expect(await screen.findByText("Реквизиты рекламодателя для маркировки")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Перейти в Яндекс Директ" })).toBeInTheDocument();
    await user.click(button("Продолжить"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Укажите реквизиты рекламодателя в кабинете Директа");
    expect(store.state.step).toBe(5);
  });

  it("реквизиты из Директа отображаются подтверждёнными и позволяют идти дальше", async () => {
    const api = wizardApi({
      marking: vi.fn(async () => ({
        saved: { kind: "legal" as const, inn: "••••3893", name: "ООО «Ноготки»", phone: null, okved: "96.02" },
      })),
    });
    const { store, user } = wizard(5, api);
    expect(await screen.findByText(/Реквизиты указаны в Директе: ИНН ••••3893/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверено" })).toBeInTheDocument();
    await user.click(button("Продолжить"));
    expect(store.state.step).toBe(6);
  });

  it("код статистики проверяет сервис и ждёт воркер", async () => {
    let calls = 0;
    const tagCheck = vi.fn(async () => (calls++ === 0 ? { check: null, site: "https://nogti.ru/" } : { check: found, site: "https://nogti.ru/" }));
    const api = wizardApi({ tagCheck });
    const { store, user } = wizard(6, api);
    await user.click(await screen.findByRole("button", { name: "Проверить установку" }));
    expect(api.requestTagCheck).toHaveBeenCalled();
    expect(await screen.findByText(/Код найден на сайте, счётчик 98765432/)).toBeInTheDocument();
    expect(store.state.metrica).toBe(true);
  });

  it("кода нет: честно про Tag Manager, письмо с адресом сайта", async () => {
    const api = wizardApi({ tagCheck: vi.fn(async () => ({ check: { ...found, status: "missing" as const, counters: [] }, site: "https://nogti.ru/" })) });
    const { user } = wizard(6, api);
    expect(await screen.findByText(/Google Tag Manager/)).toBeInTheDocument();
    await user.click(button("Скопировать письмо разработчику"));
    expect(screen.getByText(/на сайт https:\/\/nogti.ru\/ код счётчика/)).toBeInTheDocument();
  });

  it("без сайта код не нужен", async () => {
    const { store } = wizard(6, wizardApi({ tagCheck: vi.fn(async () => ({ check: null, site: "" })) }));
    expect(await screen.findByText(/В профиле нет сайта/)).toBeInTheDocument();
    expect(store.state.metrica).toBe(true);
  });

  it("сводка от сервиса: что готово, чего нет, запуск честно ждёт доступа", async () => {
    const launchCheck = vi.fn(async () => ({
      items: [
        { key: "plan" as const, ok: true, text: "Бюджет: 20 000 ₽ в месяц" },
        { key: "account" as const, ok: false, text: "Кабинет Директа не подключён" },
      ],
      ready: false,
      launch_available: false,
      launch_text: "Создавать кампании в Яндексе сервис начнёт, когда Яндекс откроет доступ к API.",
    }));
    const { saveDraft } = await import("../draft");
    const { store, user } = wizard(7, wizardApi({ launchCheck }));
    saveDraft({ site: "x", facts: [], budget: 1 });
    expect(heading("Что готово к запуску")).toBeInTheDocument();
    const list = within(await screen.findByRole("list", { name: "Готовность к запуску" }));
    expect(list.getByText(/Бюджет: 20 000 ₽/)).toBeInTheDocument();
    expect(list.getByRole("img", { name: "не готово" })).toBeInTheDocument();
    expect(screen.getByText(/когда Яндекс откроет доступ к API/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Запустить рекламу" })).not.toBeInTheDocument();
    await user.click(button("Открыть кабинет"));
    expect(store.navigate).toHaveBeenCalledWith("/");
    expect(loadDraft()).toBeNull();
  });

  it("запуск на сервисе: кнопка создаёт кампании и сводка говорит, что дальше", async () => {
    const ready = { key: "launch_plan" as const, ok: true, text: "Запуск: 1 кампания (поиск и сайты — 18 000 ₽) до 02.12" };
    const launchCheck = vi
      .fn()
      .mockResolvedValueOnce({ items: [ready], ready: true, launch_available: true, launched: null, launch_text: "Нажмите «Запустить рекламу»" })
      .mockResolvedValueOnce({ items: [ready], ready: true, launch_available: false, launched: "done", launch_text: "Реклама запущена: Яндекс проверяет объявления" });
    const startLaunch = vi.fn(async () => ({ status: "done" as const, campaigns: [{ name: "Ноготок: поиск и сайты (ah-1-1)", limit_rubles: 18000 }], text: "" }));
    const { user } = wizard(7, wizardApi({ launchCheck, startLaunch }));
    await user.click(await screen.findByRole("button", { name: "Запустить рекламу" }));
    expect(startLaunch).toHaveBeenCalledTimes(1);
    const made = within(await screen.findByRole("list", { name: "Созданные кампании" }));
    expect(made.getByText(/лимит 18 000 ₽ на месяц/)).toBeInTheDocument();
    expect(screen.getByText(/Реклама запущена/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Запустить рекламу" })).not.toBeInTheDocument();
  });

  it("отказ запуска показан словами сервиса, кнопка остаётся", async () => {
    const launchCheck = vi.fn(async () => ({ items: [], ready: true, launch_available: true, launched: null, launch_text: "" }));
    const startLaunch = vi.fn(async () => {
      throw new Error("В кабинете Яндекса уже есть кампании с лимитами");
    });
    const { user } = wizard(7, wizardApi({ launchCheck, startLaunch }));
    await user.click(await screen.findByRole("button", { name: "Запустить рекламу" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже есть кампании");
    expect(screen.getByRole("button", { name: "Запустить рекламу" })).toBeEnabled();
  });

  it("возврат с подключения кабинета продолжает тот же бизнес", async () => {
    const { saveWizard } = await import("../draft");
    saveWizard("b1");
    const store = new MockStore(initialState("start"));
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.navigate = vi.fn();
    const open = vi.fn(() => wizardApi());
    render(<Start store={store} />);
    const session = vi.fn(async () => ({ me: { email: "anna@example.ru", csrf_token: "c" }, businesses: [{ id: "other", name: "x" }, { id: "b1", name: "nogti.ru" }] }));
    await act(() => store.resumeConnect({ session, open }, "Подключить не получилось: ссылка устарела или Яндекс отказал."));
    expect(open).toHaveBeenCalledWith("b1", "c");
    expect(store.state.step).toBe(4);
    expect(screen.getByRole("alert")).toHaveTextContent("Подключить не получилось");
  });

  it("возврат без запуска в этом браузере ведёт в кабинет", async () => {
    clearWizard();
    const store = new MockStore(initialState("start"));
    store.navigate = vi.fn();
    const session = vi.fn(async () => ({ me: { email: null, csrf_token: "c" }, businesses: [{ id: "b1", name: "x" }] }));
    await store.resumeConnect({ session, open: vi.fn() }, null);
    expect(store.navigate).toHaveBeenCalledWith("/");
  });
});

describe("объявления мастера на сервисе", () => {
  const facts = [
    { id: "site0", field: "niche" as const, label: "Чем занимаетесь", value: "маникюр", source: "с сайта", quote: "Студия маникюра" },
    { id: "site1", field: "service" as const, label: "Услуга", value: "Маникюр, 1 500 ₽", source: "с сайта", quote: "Маникюр — 1 500 ₽" },
  ];
  const written: AdSet = {
    id: 7, status: "ready", source: "model", template: false, needs_review: false, problem_text: null,
    ads: [
      { headline: "Маникюр за 1500 ₽", text: "Стерильные инструменты, ул. Ленина, 5.", facts: ["цена услуги «Маникюр»", "услуга из профиля"] },
      { headline: "Маникюр рядом с домом", text: "Запишитесь онлайн: ул. Ленина, 5.", facts: [] },
    ],
  };

  function withWriter(over: Partial<GuestWriter> = {}, patch: Parameters<Store["set"]>[0] = {}) {
    clearDraft();
    const writer: GuestWriter = {
      writeAds: vi.fn(async () => ({ id: 7, token: "k", status: "queued" })),
      adSet: vi.fn(async () => written),
      ...over,
    };
    const store = new MockStore({ ...initialState("start"), facts, site: "https://nogti.ru/", step: 2, ...patch });
    store.reader = { readSite: vi.fn(), siteRead: vi.fn() };
    store.writer = writer;
    store.navigate = vi.fn();
    store.waitMs = 1;
    render(<Start store={store} />);
    return { store, writer, user: userEvent.setup() };
  }

  it("пишет объявления по фактам черновика и показывает, на что они опираются", async () => {
    const { store, writer, user } = withWriter();
    await user.click(button("Продолжить"));
    expect(writer.writeAds).toHaveBeenCalledWith({
      niche: "маникюр", address: "", services: [{ name: "Маникюр", price: 1500 }], features: [],
    });
    expect(await screen.findByText("Маникюр за 1500 ₽")).toBeInTheDocument();
    expect(store.state.step).toBe(3);
    expect(screen.getByText(/цена услуги «Маникюр», услуга из профиля/)).toBeInTheDocument();
    // Изображений у настоящих объявлений пока нет: подпись шага этого не обещает.
    expect(screen.getByText(/Изображения добавлю позже/)).toBeInTheDocument();
    expect(screen.getByText(/не опирается на факты профиля/i)).toBeInTheDocument();
    // Переделать по просьбе пока может только прототип: настоящий писатель пишет набор целиком.
    expect(screen.queryByRole("button", { name: /Переделать/ })).not.toBeInTheDocument();
  });

  it("пока пишет, дальше не пускает", async () => {
    let finish: (v: AdSet) => void = () => undefined;
    const { store, user } = withWriter({ adSet: vi.fn(() => new Promise<AdSet>((resolve) => { finish = resolve; })) });
    await user.click(button("Продолжить"));
    expect(await screen.findByText(/Пишу объявления по вашим фактам/)).toBeInTheDocument();
    await user.click(button("Нравится"));
    expect(store.state.step).toBe(3);
    expect(screen.getByRole("alert")).toHaveTextContent("ещё пишутся");
    await act(async () => finish(written));
    expect(await screen.findByText("Маникюр рядом с домом")).toBeInTheDocument();
  });

  it("сбой писателя объясняет и даёт написать ещё раз", async () => {
    const failed: AdSet = { ...written, status: "failed", ads: [], problem_text: "Не получилось написать объявления. Попробуйте ещё раз" };
    const adSet = vi.fn<GuestWriter["adSet"]>().mockResolvedValueOnce(failed).mockResolvedValue(written);
    const { writer, user } = withWriter({ adSet });
    await user.click(button("Продолжить"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не получилось написать");
    await user.click(button("Написать ещё раз"));
    expect(writer.writeAds).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Маникюр за 1500 ₽")).toBeInTheDocument();
  });

  it("лимит писателя показывает словами сервиса", async () => {
    const { user } = withWriter({ writeAds: vi.fn(async () => { throw new ApiError(429, "С этого адреса объявления сегодня писали уже 3 раза"); }) });
    await user.click(button("Продолжить"));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже 3 раза");
  });

  it("шаблон и строгая сфера сказаны честно", async () => {
    const { user } = withWriter({ adSet: vi.fn(async (): Promise<AdSet> => ({ ...written, source: "template", template: true, needs_review: true })) });
    await user.click(button("Продолжить"));
    expect(await screen.findByText(/собраны по шаблону из ваших фактов/)).toBeInTheDocument();
    expect(screen.getByText(/каждое объявление покажу вам/)).toBeInTheDocument();
  });

  it("назад и вперёд без правки фактов не пишет заново", async () => {
    const { store, writer, user } = withWriter();
    await user.click(button("Продолжить"));
    await screen.findByText("Маникюр за 1500 ₽");
    act(() => store.set({ step: 2 }));
    await user.click(button("Продолжить"));
    expect(writer.writeAds).toHaveBeenCalledTimes(1);
    act(() => store.set({ step: 2, facts: [...facts, { id: "n1", label: "Вы добавили", value: "Парковка", source: "от вас" }] }));
    await user.click(button("Продолжить"));
    expect(writer.writeAds).toHaveBeenCalledTimes(2);
  });

  it("вход уносит выбор: какие объявления владелец оставил", async () => {
    const { store, user } = withWriter();
    await user.click(button("Продолжить"));
    await user.click(await screen.findByRole("button", { name: "Убрать «Маникюр за 1500 ₽»" }));
    await user.click(button("Нравится"));
    expect(store.state.step).toBe(4);
    await user.click(button("Войти через Яндекс"));
    expect(loadDraft()?.ads).toEqual({ id: 7, token: "k", kept: [1] });
  });

  it("после входа выбор уже в сервисе: кнопок выбора нет", async () => {
    const { user } = withWriter({}, { account: { email: "anna@example.ru" } });
    await user.click(button("Продолжить"));
    await screen.findByText("Маникюр за 1500 ₽");
    expect(screen.queryByRole("button", { name: /Убрать/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Выбор сохранён в сервисе/)).toBeInTheDocument();
  });

  it("без услуг в профиле дальше не пускает, позволяет добавить услугу и перейти дальше", async () => {
    const factsWithoutServices = [
      { id: "f1", field: "niche" as const, label: "Чем занимаетесь", value: "Салон красоты", source: "от вас" },
      { id: "f2", field: "address" as const, label: "Где", value: "Пермь", source: "от вас" },
    ];
    const { store, user } = setup({ step: 1, facts: factsWithoutServices });

    expect(screen.getByText(/Добавьте хотя бы одну услугу/)).toBeInTheDocument();

    await user.click(button("Всё верно"));
    expect(store.state.step).toBe(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Добавьте хотя бы одну услугу");

    await user.click(button("Добавить факт"));
    await user.type(screen.getByLabelText("Новый факт"), "Стрижка, 1 500 ₽");
    await user.click(button("Сохранить"));

    expect(screen.getByText("Стрижка, 1 500 ₽")).toBeInTheDocument();

    await user.click(button("Всё верно"));
    expect(store.state.step).toBe(2);
  });

  it("на шаге объявлений при ошибке есть кнопка возврата в профиль", async () => {
    const { store, user } = setup({ step: 3, ads: [], block: "Добавьте хотя бы одну услугу: объявления пишу по услугам" });
    expect(screen.getByText("К услугам")).toBeInTheDocument();
    await user.click(screen.getByText("К услугам"));
    expect(store.state.step).toBe(1);
  });

  describe("formatSite", () => {
    it("очищает url от протоколов, www и слэшей", () => {
      expect(formatSite("https://salebot.site/st/bigoodi_perm")).toBe("salebot.site/st/bigoodi_perm");
      expect(formatSite("http://www.salebot.site/st/bigoodi_perm/")).toBe("salebot.site/st/bigoodi_perm");
      expect(formatSite("https://nogti.ru/")).toBe("nogti.ru");
      expect(formatSite("salon-primer.ru")).toBe("salon-primer.ru");
      expect(formatSite("   https://test.ru   ")).toBe("test.ru");
      expect(formatSite("")).toBe("");
    });

    it("декодирует кириллические url", () => {
      expect(formatSite("https://салон-красоты.рф/цены/")).toBe("салон-красоты.рф/цены");
      expect(formatSite("https://example.com/%D1%82%D0%B5%D1%81%D1%82")).toBe("example.com/тест");
    });
  });
});
