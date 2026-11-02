import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { SiteRead } from "../../api";
import { initialState, Store } from "../store";
import type { GuestReader } from "../store";
import { Start } from "./Start";

function setup(patch: Parameters<Store["set"]>[0] = {}) {
  const store = new Store({ ...initialState("start"), ...patch });
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
    expect(screen.getByText("Примерно 50–70 обращений")).toBeInTheDocument();
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
    await user.click(button("Войти через Яндекс"));
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
    await user.click(button("Войти через Яндекс"));
    expect(screen.getByText("Подключён: логин salon-primer")).toBeInTheDocument();
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
    expect(screen.getByText("Примерно 40–55 обращений")).toBeInTheDocument();
    expect(screen.getByText(/Это оценка, а не обещание/)).toBeInTheDocument();
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

  it("чат открывается плашкой, постоянной строки чата нет", async () => {
    const { store, user } = setup({ step: 1 });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(button("Можно спросить в чате"));
    expect(store.state.mode).toBe("chat");
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
    const store = new Store(initialState("start"));
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
    expect(reader.siteRead).toHaveBeenCalledWith(7, queued.token);
    expect(screen.getByText(/^Маникюр с покрытием, 1.500.₽$/)).toBeInTheDocument();
    expect(screen.getByText("«Маникюр с покрытием — 1 500 ₽»")).toBeInTheDocument();
    expect(screen.getByText("Оплата картой")).toBeInTheDocument();
    // Подставных фактов прототипа здесь нет: только найденное на сайте.
    expect(screen.queryByText("Скидка 20% на первый визит")).not.toBeInTheDocument();
    expect(store.state.site).toBe("https://nogti.ru/");
  });

  it("пока сайт читается, второй разбор не ставится", async () => {
    const { reader, user } = withReader({ siteRead: vi.fn(() => new Promise<SiteRead>(() => undefined)) });
    await user.type(screen.getByLabelText("Ссылка на сайт или страницу"), "nogti.ru");
    await user.click(button("Изучить"));
    expect(await screen.findByRole("status")).toHaveTextContent("Читаю сайт");
    await user.click(button("Изучить"));
    expect(reader.readSite).toHaveBeenCalledTimes(1);
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
