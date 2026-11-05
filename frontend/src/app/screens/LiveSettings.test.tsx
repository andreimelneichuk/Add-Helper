import { StrictMode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { Api, Profile } from "../../api";
import { fakeApi, open, overview, settled } from "../testLive";
import { savedText } from "../live";
import { mergeFacts } from "./LiveSettings";
import { Settings } from "./Settings";

afterEach(() => vi.unstubAllGlobals());

describe("настройки на настоящих данных", () => {
  it("правка профиля уходит в API и перечитывает данные экранов", async () => {
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const price = await screen.findByLabelText("Цена услуги 1, ₽");
    await userEvent.clear(price);
    await userEvent.type(price, "1400");
    await userEvent.click(screen.getByRole("button", { name: "Добавить услугу" }));
    await userEvent.type(screen.getByLabelText("Услуга 2"), "Педикюр");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Сохранил");
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({ services: [{ name: "Маникюр", price: 1400 }, { name: "Педикюр", price: null }] }));
    // Профиль перечитан вместе с остальными данными: два раза при открытии экрана и загрузке, ещё раз после сохранения.
    expect(vi.mocked(api.profile).mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("запоздавший ответ о профиле не затирает то, что владелец уже ввёл", async () => {
    const base = await fakeApi().profile();
    let late: (p: Profile) => void = () => undefined;
    const profile = vi.fn()
      .mockImplementationOnce(async () => base)
      .mockImplementationOnce(async () => base)
      .mockImplementationOnce(() => new Promise<Profile>((resolve) => { late = resolve; }))
      .mockImplementation(async () => base);
    const api = fakeApi({ profile } as Partial<Api>);
    const store = await open(api);
    // В режиме разработки эффект запускается дважды: второй ответ приходит, когда владелец уже печатает.
    render(<StrictMode><Settings store={store} /></StrictMode>);
    const price = await screen.findByLabelText("Цена услуги 1, ₽");
    await userEvent.clear(price);
    await userEvent.type(price, "1400");
    late(base);
    await settled();
    expect(profile).toHaveBeenCalledTimes(3);
    expect(screen.getByLabelText("Цена услуги 1, ₽")).toHaveValue("1400");
  });

  it("после правки цены называет объявления со старыми цифрами и что с ними будет", async () => {
    const outdated = [{ account: "lak", ad_id: "11", headline: "Маникюр 1200 ₽", text: "У метро", numbers: ["1200"], replacing: true }];
    const api = fakeApi({ saveProfile: vi.fn(async (p: Profile) => ({ ...p, outdated })) });
    render(<Settings store={await open(api)} />);
    const price = await screen.findByLabelText("Цена услуги 1, ₽");
    await userEvent.clear(price);
    await userEvent.type(price, "1400");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("«Маникюр 1200 ₽» (1200)");
    expect(status).toHaveTextContent("Пишу замену");
    expect(status).toHaveTextContent("старое объявление показывается");
  });

  it("текст после сохранения: без устаревших и при исчерпанном лимите", () => {
    expect(savedText([])).toContain("старых цифр нет");
    const later = savedText([{ account: "lak", ad_id: "11", headline: "Маникюр 1200 ₽", text: "", numbers: ["1200"], replacing: false }]);
    expect(later).toContain("замену напишу завтра");
    expect(later).not.toContain("Пишу замену");
  });

  it("пустую услугу не сохраняет, отказ сервиса показывает как есть", async () => {
    const api = fakeApi({ saveProfile: vi.fn(async () => { throw new ApiError(422, "Слишком длинный адрес"); }) } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    await userEvent.click(await screen.findByRole("button", { name: "Добавить услугу" }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Слишком длинный адрес");
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({ services: [{ name: "Маникюр", price: 1200 }] }));
  });

  it("показывает кабинет и ведёт на подключение в Яндекс", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const account = screen.getByRole("region", { name: "Кабинет Яндекса" });
    expect(await within(account).findByText("anna")).toBeInTheDocument();
    expect(within(account).getByText("Подключён")).toBeInTheDocument();
    await userEvent.click(within(account).getByRole("button", { name: "Подключить заново" }));
    expect(assign).toHaveBeenCalledWith("https://oauth.yandex.ru/authorize?x=1");
  });

  it("без кабинета объясняет, зачем он нужен", async () => {
    render(<Settings store={await open(fakeApi({ accounts: vi.fn(async () => []) } as Partial<Api>))} />);
    expect(await screen.findByText(/без него реклама не запустится/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подключить кабинет" })).toBeInTheDocument();
  });

  it("подключает Telegram и проверяет привязку", async () => {
    const opened = vi.fn();
    vi.stubGlobal("open", opened);
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const channels = screen.getByRole("region", { name: "Уведомления" });
    expect(await within(channels).findByText("anna@example.ru")).toBeInTheDocument();
    await userEvent.click(within(channels).getByRole("button", { name: "Подключить" }));
    expect(api.linkTelegram).toHaveBeenCalledOnce();
    expect(opened).toHaveBeenCalledWith("https://t.me/bot?start=x", "_blank", "noopener");
    expect(within(channels).getByText(/Откройте бота/)).toBeInTheDocument();
  });

  it("выход закрывает сессию на сервере", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(api.logout).toHaveBeenCalledOnce();
    expect(assign).toHaveBeenCalledWith("/");
  });
});

describe("бюджет на настоящих данных", () => {
  it("снижение спрашивает подтверждение и объясняет порядок, затем уходит в сервис", async () => {
    const api = fakeApi({ setBudget: vi.fn(async () => ({ status: "applied", cuts: 1 })) } as Partial<Api>);
    const store = await open(api);
    render(<Settings store={store} />);
    const input = screen.getByLabelText("Бюджет в месяц, ₽");
    await userEvent.clear(input);
    await userEvent.type(input, "15000");
    await userEvent.click(screen.getByRole("button", { name: "Изменить бюджет" }));
    expect(api.setBudget).not.toHaveBeenCalled();
    const confirm = screen.getByRole("group", { name: "Подтверждение бюджета" });
    expect(confirm).toHaveTextContent("Сначала снижу лимиты в Яндексе");
    await userEvent.click(within(confirm).getByRole("button", { name: "Применить" }));
    await settled();
    expect(api.setBudget).toHaveBeenCalledWith(15000, expect.any(String));
    expect(store.state.notice).toContain("Бюджет месяца теперь 15 000 ₽");
  });

  it("ниже потраченного не даёт отправить", async () => {
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const input = screen.getByLabelText("Бюджет в месяц, ₽");
    await userEvent.clear(input);
    await userEvent.type(input, "1000");
    expect(screen.getByRole("alert")).toHaveTextContent("уже потрачено 1 500 ₽");
    expect(screen.getByRole("button", { name: "Изменить бюджет" })).toBeDisabled();
  });

});

describe("где и когда показывать", () => {
  it("регион и часы меняет только после подтверждения и пишет, как отменить", async () => {
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const block = await screen.findByRole("region", { name: "Где и когда показывать" });
    expect(await within(block).findByText(/Сейчас: Москва/)).toBeInTheDocument();
    await userEvent.click(within(block).getByRole("button", { name: "Санкт-Петербург" }));
    await userEvent.click(within(block).getByRole("button", { name: "Изменить регион" }));
    const ask = within(block).getByRole("group", { name: "Подтверждение показа" });
    expect(ask).toHaveTextContent("Москва, Санкт-Петербург вместо «Москва»");
    expect(api.setRegions).not.toHaveBeenCalled();
    await userEvent.click(within(ask).getByRole("button", { name: "Применить" }));
    expect(api.setRegions).toHaveBeenCalledWith([213, 2], expect.any(String));
    expect(await within(block).findByRole("status")).toHaveTextContent("Отменить можно в истории");

    await userEvent.click(within(block).getByRole("button", { name: "вс" }));
    await userEvent.selectOptions(within(block).getByLabelText("Показывать с"), "10");
    await userEvent.selectOptions(within(block).getByLabelText("Показывать до"), "21");
    await userEvent.click(within(block).getByRole("button", { name: "Изменить время" }));
    await userEvent.click(within(block).getByRole("button", { name: "Не менять" }));
    expect(api.setSchedule).not.toHaveBeenCalled();
    await userEvent.click(within(block).getByRole("button", { name: "Изменить время" }));
    expect(within(block).getByRole("group", { name: "Подтверждение показа" })).toHaveTextContent("пн–сб, 10:00–21:00");
    await userEvent.click(within(block).getByRole("button", { name: "Применить" }));
    expect(api.setSchedule).toHaveBeenCalledWith([0, 1, 2, 3, 4, 5], 10, 21, expect.any(String));
  });

  it("расписание, которое Яндекс не примет, отправить нельзя", async () => {
    render(<Settings store={await open(fakeApi())} />);
    const block = await screen.findByRole("region", { name: "Где и когда показывать" });
    await within(block).findByText(/Сейчас: круглосуточно/);
    for (const day of ["пн", "вт", "ср", "чт", "пт", "сб"]) await userEvent.click(within(block).getByRole("button", { name: day }));
    await userEvent.selectOptions(within(block).getByLabelText("Показывать с"), "10");
    await userEvent.selectOptions(within(block).getByLabelText("Показывать до"), "17");
    expect(within(block).getByRole("alert")).toHaveTextContent("не принимает меньше 8 часов показа в неделю, сейчас 7");
    expect(within(block).getByRole("button", { name: "Изменить время" })).toBeDisabled();
  });

  it("отказ сервиса при смене региона показывает как есть", async () => {
    const api = fakeApi({ setRegions: vi.fn(async () => { throw new ApiError(503, "Кабинет Яндекса пока не подключён, поэтому ничего не изменилось"); }) } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    const block = await screen.findByRole("region", { name: "Где и когда показывать" });
    await userEvent.click(await within(block).findByRole("button", { name: "Москва и область" }));
    await userEvent.click(within(block).getByRole("button", { name: "Изменить регион" }));
    await userEvent.click(within(block).getByRole("button", { name: "Применить" }));
    expect(await within(block).findByRole("status")).toHaveTextContent("ничего не изменилось");
  });

  it("желаемую цену обращения сохраняет в рублях", async () => {
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const field = await screen.findByLabelText("Желаемая цена обращения, ₽");
    await userEvent.type(field, "450");
    await userEvent.click(screen.getByRole("button", { name: "Задать цену" }));
    expect(api.setGoal).toHaveBeenCalledWith(450);
    expect(await screen.findByText(/теперь 450/)).toBeInTheDocument();
  });
});


const ready = {
  id: 1,
  url: "https://nogti.ru/",
  status: "ready" as const,
  problem: null,
  problem_text: null,
  pages: ["https://nogti.ru/"],
  facts: [
    { field: "niche" as const, value: "маникюр", price: null, quote: "Студия маникюра «Ноготок»", page: "https://nogti.ru/" },
    { field: "service" as const, value: "маникюр", price: 1500, quote: "Маникюр — 1 500 ₽", page: "https://nogti.ru/" },
    { field: "service" as const, value: "Педикюр", price: 2000, quote: "Педикюр — 2 000 ₽", page: "https://nogti.ru/" },
    { field: "feature" as const, value: "Работаем с 2015 года", price: null, quote: "Работаем с 2015 года", page: "https://nogti.ru/" },
  ],
};

describe("заполнение профиля с сайта", () => {
  it("слияние: отмеченная цена заменяет прежнюю, новые факты добавляются в конец", () => {
    const profile = { services: [{ name: "Маникюр", price: 1200 }], address: "Москва", features: ["Чай"], niche: "", website: "" };
    expect(mergeFacts(profile, ready.facts, ready.url)).toEqual({
      services: [{ name: "Маникюр", price: 1500 }, { name: "Педикюр", price: 2000 }],
      address: "Москва",
      features: ["Чай", "Работаем с 2015 года"],
      niche: "маникюр",
      website: "https://nogti.ru/",
    });
  });

  it("находит факты с цитатами, переносит выбранные в форму, сохраняет владелец", async () => {
    const siteRead = vi.fn().mockResolvedValueOnce(null).mockResolvedValue(ready);
    const api = fakeApi({ siteRead } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    await userEvent.type(await screen.findByLabelText("Сайт"), "nogti.ru");
    await userEvent.click(screen.getByRole("button", { name: "Заполнить с сайта" }));
    expect(api.readSite).toHaveBeenCalledWith("nogti.ru");

    const found = await screen.findByRole("group", { name: "Нашёл на сайте" });
    expect(within(found).getByText("«Педикюр — 2 000 ₽»")).toBeInTheDocument();
    // Особенность владелец не подтвердил: снимает отметку.
    await userEvent.click(within(found).getByRole("checkbox", { name: /Работаем с 2015 года/ }));
    await userEvent.click(within(found).getByRole("button", { name: "Перенести в профиль" }));

    expect(screen.queryByRole("group", { name: "Нашёл на сайте" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Чем занимаетесь")).toHaveValue("маникюр");
    expect(screen.getByLabelText("Услуга 2")).toHaveValue("Педикюр");
    expect(screen.getByRole("status")).toHaveTextContent("нажмите «Сохранить»");
    // Черновик сам ничего не сохраняет.
    expect(api.saveProfile).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({
      niche: "маникюр",
      services: [{ name: "Маникюр", price: 1500 }, { name: "Педикюр", price: 2000 }],
      features: [],
    }));
  });

  it("неудачу объясняет словами сервиса и предлагает заполнить вручную", async () => {
    const failed = { ...ready, status: "failed" as const, facts: [], problem: "unreachable", problem_text: "Сайт не открылся. Проверьте адрес или заполните профиль вручную" };
    const api = fakeApi({ siteRead: vi.fn().mockResolvedValueOnce(null).mockResolvedValue(failed) } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    await userEvent.type(await screen.findByLabelText("Сайт"), "nogti.ru");
    await userEvent.click(screen.getByRole("button", { name: "Заполнить с сайта" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Сайт не открылся");
  });

  it("без адреса кнопка не активна", async () => {
    render(<Settings store={await open(fakeApi())} />);
    expect(await screen.findByRole("button", { name: "Заполнить с сайта" })).toBeDisabled();
  });
});

describe("план бюджета до запуска", () => {
  const noPeriod = () => ({ ...overview(), money: [] });

  it("показывает сумму из мастера и меняет её в сервисе", async () => {
    const api = fakeApi({ overview: vi.fn(async () => noPeriod()), budgetPlan: vi.fn(async () => ({ rubles: 20000 })), saveBudgetPlan: vi.fn(async (rubles: number) => ({ rubles })) } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    const field = await screen.findByLabelText("План на запуск, ₽ в месяц");
    expect(field).toHaveValue("20000");
    expect(screen.getByText(/лимиты в Яндексе поставлю при запуске/i)).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "25000");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить план" }));
    expect(api.saveBudgetPlan).toHaveBeenCalledWith(25000);
    expect(await screen.findByText(/Сохранил план: 25.000.₽/)).toBeInTheDocument();
  });

  it("отказ сервиса по сумме показывает как есть", async () => {
    const api = fakeApi({
      overview: vi.fn(async () => noPeriod()),
      budgetPlan: vi.fn(async () => ({ rubles: null })),
      saveBudgetPlan: vi.fn(async () => { throw new ApiError(422, "Яндекс не примет бюджет меньше 1 550 ₽ в месяц"); }),
    } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    const field = await screen.findByLabelText("План на запуск, ₽ в месяц");
    await userEvent.type(field, "1000");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить план" }));
    expect(await screen.findByText(/не примет бюджет меньше/)).toBeInTheDocument();
  });
  it("фото добавляется только с согласием, ошибку сервера показывает его словами", async () => {
    const photo = { id: 7, width: 600, height: 600, url: "/api/businesses/b1/photos/7" };
    const photos = vi.fn().mockResolvedValueOnce({ photos: [], max: 5 }).mockResolvedValue({ photos: [photo], max: 5 });
    const uploadPhoto = vi.fn()
      .mockRejectedValueOnce(new ApiError(422, "Фото маленькое: нужно не меньше 450 точек по короткой стороне."))
      .mockResolvedValue({ id: 7, url: photo.url });
    const api = fakeApi({ photos, uploadPhoto } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    const add = await screen.findByRole("button", { name: "Добавить фото" });
    expect(add).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/согласны на рекламу/));
    expect(add).toBeEnabled();
    const file = new File(["x"], "salon.jpg", { type: "image/jpeg" });

    await userEvent.upload(screen.getByLabelText("Файл фото"), file);
    expect(await screen.findByRole("alert")).toHaveTextContent("450 точек");
    await userEvent.upload(screen.getByLabelText("Файл фото"), file);

    expect(await screen.findByRole("status")).toHaveTextContent("Фото добавил");
    expect(uploadPhoto).toHaveBeenLastCalledWith(file, true);
    expect(within(screen.getByRole("list", { name: "Ваши фото" })).getByAltText("Фото 1")).toHaveAttribute("src", photo.url);
  });

  it("убранное фото пропадает из подбора, а полный набор просит убрать одно", async () => {
    const all = [1, 2, 3, 4, 5].map((id) => ({ id, width: 600, height: 600, url: `/p/${id}` }));
    const photos = vi.fn().mockResolvedValueOnce({ photos: all, max: 5 }).mockResolvedValue({ photos: all.slice(1), max: 5 });
    const api = fakeApi({ photos } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    expect(await screen.findByText(/Уберите одно, чтобы добавить новое/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить фото" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Убрать фото 1" }));

    expect(api.archivePhoto).toHaveBeenCalledWith(1);
    expect(await screen.findByRole("status")).toHaveTextContent("работают дальше");
    expect(screen.getByRole("button", { name: "Добавить фото" })).toBeDisabled();
  });
});
