import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {  rub } from "../data";
import { mockFixtures } from "../../mock/fixtures";
const { DAYS } = mockFixtures;
import type { Situation } from "../data";
import { initialState, week} from "../store";
import { MockStore } from "../../mock/MockStore";
import { fakeApi, open, overview } from "../testLive";
import { Overview } from "./Overview";

function show(situation: Situation = "running") {
  const store = new MockStore(initialState("work", situation));
  render(<Overview store={store} />);
  return store;
}
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("баннер внимания", () => {
  const banners: [Situation, string, string[]][] = [
    ["first", "День 2 из примерно 14: выводы делать рано", []],
    ["problem", "На счёте в Яндексе 0 ₽, показы стоят", ["Я пополнил"]],
    ["verdict", "Проверка закончена: скидка лучше цены", ["Посмотреть и отменить"]],
    ["budget", "Осталось 700 ₽ на 3 дня", ["Добавить 3 000 ₽", "Оставить"]],
    ["rejected", "Яндекс отклонил объявление, я уже исправил", ["Посмотреть в истории"]],
    ["access", "Пропал доступ к кабинету Яндекса", ["Подключить заново"]],
  ];
  it.each(banners)("%s: верный заголовок и решения", (situation, title, actions) => {
    show(situation);
    const banner = within(screen.getByRole("status"));
    expect(banner.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(banner.queryAllByRole("button").map((b) => b.textContent)).toEqual(actions);
  });

  it("running: тревоги нет", () => {
    show("running");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("«Я пополнил» убирает баннер и показывает деньги на счёте", () => {
    const store = show("problem");
    expect(screen.getByText(/^На счёте 0 ₽/)).toBeInTheDocument();
    click("Я пополнил");
    expect(store.state.topped).toBe(true);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText(/^На счёте 7 200 ₽/)).toBeInTheDocument();
  });

  it("«Подключить заново» убирает баннер", () => {
    show("access");
    click("Подключить заново");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("«Добавить 3 000 ₽» поднимает бюджет в показателе", () => {
    const store = show("budget");
    click("Добавить 3 000 ₽");
    expect(store.state.decided).toBe("added");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("из 23 000 ₽, осталось 3 700 ₽")).toBeInTheDocument();
  });

  it("«Оставить» не меняет бюджет", () => {
    const store = show("budget");
    click("Оставить");
    expect(store.state.decided).toBe("kept");
    expect(store.state.extra).toBe(0);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("«Посмотреть в истории» открывает историю", () => {
    const store = show("rejected");
    click("Посмотреть в истории");
    expect(store.state.view).toBe("history");
  });
});

describe("показатели", () => {
  it("показывают расход, обращения и цену из данных", () => {
    show("running");
    expect(screen.getByText("Потрачено в ноябре")).toBeInTheDocument();
    expect(screen.getByText(rub(8400))).toBeInTheDocument();
    expect(screen.getByText("из 20 000 ₽, осталось 11 600 ₽")).toBeInTheDocument();
    expect(screen.getByText("Обращения, 7 дней")).toBeInTheDocument();
    expect(screen.getByText("было 6")).toHaveClass("ov-ink-good");
    expect(screen.getByText(rub(420))).toBeInTheDocument();
    expect(screen.getByText("было 560 ₽, цель 400 ₽")).toBeInTheDocument();
    expect(screen.getByText("нет ответа")).toBeInTheDocument();
  });

  it("показывают показы и клики из отчёта Директа на дашборде", async () => {
    const api = fakeApi({
      today: vi.fn(async () => ({
        rules: "today-2",
        cards: [{ kind: "summary", tone: "info" as const, title: "За 7 дней показов не было", text: "Данных пока нет", action: null, ref: null, rules: "today-2" }],
      })),
    });
    const store = await open(api);
    render(<Overview store={store} />);
    expect(await screen.findByText("Показы, 7 дней")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("Клики: 10")).toBeInTheDocument();
    expect(screen.queryByText("За 7 дней показов не было")).not.toBeInTheDocument();
  });

  it("при first нет вывода о цене", () => {
    show("first");
    expect(screen.getAllByText("мало для выводов")).toHaveLength(2);
    expect(screen.getByText("Обращения, 2 дня")).toBeInTheDocument();
    expect(screen.queryByText(/цель/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^было/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Проверяю/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/стали клиентами/)).not.toBeInTheDocument();
    expect(screen.getAllByText("собираю данные")).toHaveLength(3);
    expect(screen.getAllByRole("cell", { name: "—" })).toHaveLength(9);
  });
});

describe("столбики за месяц", () => {
  it("30 настоящих кнопок: прошедшие дни можно выбрать, будущие нет", () => {
    show("running");
    const bars = screen.getAllByRole("button", { name: /^\d+ ноября/ });
    expect(bars).toHaveLength(30);
    expect(bars[13]).toBeEnabled();
    expect(bars[14]).toBeDisabled();
    expect(bars[14]).toHaveAccessibleName(`15 ноября: можно до ${rub(11600 / 16)}`);
  });

  it("выбор дня меняет подпись", () => {
    const store = show("running");
    expect(screen.getByText(/^14 ноября: 760 ₽, обращений 2\./)).toBeInTheDocument();
    click("3 ноября: 510 ₽, обращений 1");
    expect(store.state.selDay).toBe(2);
    expect(screen.getByText(/^3 ноября: 510 ₽, обращений 1\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "3 ноября: 510 ₽, обращений 1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("переключатель меняет шкалу на обращения", () => {
    const { container } = render(<Overview store={new MockStore()} />);
    expect(container.querySelector(".ov-chart-top")).toHaveTextContent(rub(1000));
    expect(container.querySelector(".ov-chart-limit")).toBeInTheDocument();
    click("Обращения");
    expect(screen.getByRole("button", { name: "Обращения" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".ov-chart-top")).toHaveTextContent("4");
    expect(container.querySelector(".ov-chart-limit")).not.toBeInTheDocument();
  });
});

describe("вопрос о клиентах", () => {
  it("ответ показывает цену клиента", () => {
    const store = show("running");
    fireEvent.change(screen.getByLabelText("Сколько из 12 обращений стали клиентами?"), { target: { value: "6" } });
    click("Записать");
    expect(store.state.clients).toBe(6);
    expect(screen.getByText(`цена клиента ${rub(week(store.state).cost / 6)}`)).toBeInTheDocument();
    expect(screen.getByText("Записал 6 клиентов. Цена клиента теперь считается.")).toBeInTheDocument();
  });

  it("число больше обращений не записывается", () => {
    const store = show("running");
    fireEvent.change(screen.getByLabelText(/стали клиентами/), { target: { value: "13" } });
    click("Записать");
    expect(store.state.clients).toBeNull();
  });

  it.each<Situation>(["problem", "access", "first"])("%s: вопроса нет", (situation) => {
    show(situation);
    expect(screen.queryByLabelText(/стали клиентами/)).not.toBeInTheDocument();
  });
});

describe("остальные блоки", () => {
  it("идущая проверка видна только при running", () => {
    const store = show("running");
    expect(screen.getByText(/день 9 из 14/)).toBeInTheDocument();
    act(() => store.pickSituation("verdict"));
    expect(screen.queryByText(/день 9 из 14/)).not.toBeInTheDocument();
  });

  it("таблица считает цену и отмечает отключённое", () => {
    const store = show("running");
    act(() => store.run({ action: "ad", value: "a1" }));
    expect(screen.getByRole("row", { name: /Первый визит со скидкой отключено 7 329 ₽ 2 300 ₽/ })).toHaveClass("ov-row-off");
  });

  it("показывает три последние записи и ведёт в историю", () => {
    const store = show("verdict");
    const items = within(screen.getByRole("region", { name: "Последние действия" })).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent(/Отключил вариант «Маникюр от 1\s200\s₽»/);
    click("Вся история и отмена");
    expect(store.state.view).toBe("history");
  });

  it("строка сверки берёт сумму счёта из данных", () => {
    show("budget");
    expect(DAYS.budget).toHaveLength(27);
    expect(screen.getByText(/^На счёте 900 ₽\. Сверка: разница 0 ₽, сегодня в 9:40\./)).toBeInTheDocument();
  });

  it("в живом режиме показывает баланс аккаунта до запуска рекламы", async () => {
    const api = fakeApi({
      overview: vi.fn(async () => ({
        ...overview(),
        money: [],
        campaigns: [],
        account_balance_micros: 12_500 * 1_000_000,
        account_login: "anna",
      })),
    });
    const store = await open(api);
    render(<Overview store={store} />);
    expect(screen.getByText(/На счёте 12\s?500\s?₽\. Сверка начнётся после запуска рекламы\./)).toBeInTheDocument();
  });

  it("в живом режиме 0 ₽ отображается корректно и не считается отсутствием данных", async () => {
    const api = fakeApi({
      overview: vi.fn(async () => ({
        ...overview(),
        money: [],
        campaigns: [],
        account_balance_micros: 0,
        account_login: "anna",
      })),
    });
    const store = await open(api);
    render(<Overview store={store} />);
    expect(screen.getByText(/На счёте 0\s?₽\. Сверка начнётся после запуска рекламы\./)).toBeInTheDocument();
  });
});

describe("лента сервиса на «Сегодня»", () => {
  it("первая карточка крупно, остальные по порядку, финал ленты", async () => {
    const cards = [
      { kind: "paused", tone: "bad" as const, title: "Реклама на паузе", text: "Вы поставили паузу.", action: "resume" as const, ref: null, rules: "today-1" },
      { kind: "summary", tone: "info" as const, title: "12 обращений за 7 дней, по 420 ₽", text: "", action: null, ref: null, rules: "today-1" },
      { kind: "last_action", tone: "info" as const, title: "Отключил объявление", text: "Вы попросили.", action: "undo" as const, ref: "change-5", rules: "today-1" },
    ];
    const api = fakeApi({ today: vi.fn(async () => ({ rules: "today-1", cards })) });
    const store = await open(api);
    render(<Overview store={store} />);
    const banner = within(screen.getByRole("status"));
    expect(banner.getByRole("heading", { name: "Реклама на паузе" })).toBeInTheDocument();
    expect(banner.getByRole("button", { name: "Включить" })).toBeInTheDocument();
    const more = within(screen.getByRole("region", { name: "Ещё на сегодня" }));
    expect(more.getAllByRole("heading").map((h) => h.textContent)).toEqual(["12 обращений за 7 дней, по 420 ₽", "Отключил объявление"]);
    expect(more.getByText("На сегодня всё.")).toBeInTheDocument();
    fireEvent.click(more.getByRole("button", { name: "Отменить" }));
    expect(store.state.view).toBe("history");
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(api.rollbackPreview).toHaveBeenCalledWith(5);
  });
  it("предложение теста: согласие уходит в сервис, его ответ виден; отмена готовящегося", async () => {
    const offer = { kind: "test_offer", tone: "info" as const, title: "Предлагаю проверить новый вариант объявления", text: "«Маникюр» потратило 1 200 ₽.", action: "test_offer" as const, ref: "41", rules: "today-2" };
    const preparing = { kind: "test_preparing", tone: "info" as const, title: "Готовлю A/B-тест", text: "Новый вариант напишу позже.", action: "test_cancel" as const, ref: "9", rules: "today-2" };
    const summary = { kind: "summary", tone: "good" as const, title: "Всё в порядке", text: "", action: null, ref: null, rules: "today-2" };
    const api = fakeApi({ today: vi.fn(async () => ({ rules: "today-2", cards: [summary, offer, preparing] })) });
    const store = await open(api);
    render(<Overview store={store} />);
    const more = within(screen.getByRole("region", { name: "Ещё на сегодня" }));

    fireEvent.click(more.getByRole("button", { name: "Проверить вариант" }));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(api.answerTest).toHaveBeenCalledWith(41, true);
    expect(store.state.notice).toBe("Готовлю тест. Пока вариант не отправлен, объявление работает как раньше.");

    fireEvent.click(more.getByRole("button", { name: "Не сейчас" }));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(api.answerTest).toHaveBeenLastCalledWith(41, false);

    fireEvent.click(more.getByRole("button", { name: "Отменить тест" }));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(api.cancelTest).toHaveBeenCalledWith(9);
    expect(store.state.notice).toBe("Тест отменён. Показы не менялись, это объявление не предложу две недели.");
  });
});
