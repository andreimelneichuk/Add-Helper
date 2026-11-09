import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { rub } from "../data";
import { initialState} from "../store";
import { MockStore } from "../../mock/MockStore";
import { Ads } from "./Ads";
import { Settings } from "./Settings";

const adCard = (name: string) => screen.getByRole("button", { name: `Открыть объявление «${name}»` });

describe("экран рекламы", () => {
  it("свой бюджет применяется кнопкой, пишется в историю, меньше минимума нельзя", async () => {
    const store = new MockStore();
    const before = store.state.hist.length;
    render(<Settings store={store} />);
    const user = userEvent.setup();
    const group = within(screen.getByRole("group", { name: "Бюджет в месяц" }));
    const field = group.getByLabelText("Своя сумма, ₽");
    await user.type(field, "500");
    expect(group.getByRole("button", { name: "Задать" })).toBeDisabled();
    await user.clear(field);
    await user.type(field, "32000");
    expect(store.state.f.budget).toBe(20000);
    await user.click(group.getByRole("button", { name: "Задать" }));
    expect(store.state.f.budget).toBe(20000);
    act(() => store.confirm());
    expect(store.state.f.budget).toBe(32000);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(group.getByText(rub(32000))).toBeInTheDocument();
  });

  it("смена настройки пишет в историю и не срабатывает повторно на выбранном варианте", async () => {
    const store = new MockStore();
    const before = store.state.hist.length;
    render(<Settings store={store} />);
    const budget = within(screen.getByRole("group", { name: "Бюджет в месяц" }));
    const option = budget.getByRole("button", { name: rub(25000) });

    await userEvent.click(option);
    expect(store.state.f.budget).toBe(20000);
    act(() => store.confirm());

    expect(store.state.f.budget).toBe(25000);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(store.state.hist[0]?.title).toContain(rub(25000));
    expect(option).toHaveAttribute("aria-pressed", "true");
    expect(budget.getByRole("button", { name: rub(20000) })).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(option);

    expect(store.state.hist).toHaveLength(before + 1);
  });

  it("отключение и включение объявления пишет в историю", async () => {
    const store = new MockStore();
    const before = store.state.hist.length;
    render(<Ads store={store} />);

    await userEvent.click(adCard("Педикюр рядом с домом"));
    await userEvent.click(screen.getByRole("button", { name: "Отключить" }));
    expect(store.state.adsOff.a3).toBeFalsy();
    act(() => store.confirm());

    expect(store.state.adsOff.a3).toBe(true);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(store.state.hist[0]?.title).toBe("Отключил «Педикюр рядом с домом»");
    expect(screen.getAllByText("Отключено")).toHaveLength(2);

    await userEvent.click(screen.getByRole("button", { name: "Включить" }));

    expect(store.state.adsOff.a3).toBe(false);
    expect(store.state.hist).toHaveLength(before + 2);
    expect(store.state.hist[0]?.title).toBe("Включил «Педикюр рядом с домом»");
  });

  it("показывает показатели объявления с верным окончанием", () => {
    render(<Ads store={new MockStore()} />);
    expect(screen.getByText(`7 обращений, по ${rub(2300 / 7)}`)).toBeInTheDocument();
    expect(screen.getByText(`2 обращения, по ${rub(590)}`)).toBeInTheDocument();
  });

  it("в первые дни не приписывает объявлениям накопленные обращения", () => {
    render(<Ads store={new MockStore(initialState("work", "first"))} />);
    expect(screen.getAllByText("Собираю данные")).toHaveLength(3);
    expect(screen.queryByText(/7 обращений/)).toBeNull();
  });

  it("новая цена видна в объявлении и в профиле", () => {
    const store = new MockStore();
    store.run({ action: "price", value: 1400 });
    render(<Ads store={store} />);
    const name = `Маникюр от ${rub(1400)}`;
    expect(screen.getAllByText(name)).toHaveLength(1);
    expect(screen.getByRole("button", { name: `Открыть объявление «${name}»` })).toBeInTheDocument();
    expect(screen.queryByText("Маникюр от 1 200 ₽")).toBeNull();
    cleanup();
    render(<Settings store={store} />);
    expect(screen.getByText(/маникюр от 1 400 ₽, педикюр от 1 800 ₽/)).toBeInTheDocument();
  });

  it("кнопка «Что-то изменилось» открывает чат с готовой фразой", async () => {
    const store = new MockStore();
    render(<Settings store={store} />);
    await userEvent.click(screen.getByRole("button", { name: "Что-то изменилось" }));
    expect(store.state.mode).toBe("chat");
    expect(store.state.input).toBe("Новая цена на маникюр 1400");
  });

  it("список не содержит настроек объявления, а вся карточка доступна как кнопка", () => {
    render(<Ads store={new MockStore()} />);
    expect(screen.queryByRole("group", { name: "Бюджет в месяц" })).toBeNull();
    expect(adCard("Первый визит со скидкой")).toHaveAttribute("tabindex", "0");
    expect(adCard("Первый визит со скидкой")).toHaveAttribute("role", "button");
  });

  it("список можно сузить до отключённых, счётчик совпадает", async () => {
    const store = new MockStore({ ...initialState(), adsOff: { a3: true } });
    render(<Ads store={store} />);
    await userEvent.click(screen.getByRole("button", { name: "Отключены 1" }));
    expect(adCard("Педикюр рядом с домом")).toHaveTextContent("Отключено");
    expect(screen.queryByRole("button", { name: "Открыть объявление «Первый визит со скидкой»" })).toBeNull();
  });

  it("отдельно показывает объявления, которые Яндекс проверяет", async () => {
    const state = initialState("work", "running");
    state.ads = state.ads.map((ad, index) => index === 1
      ? { ...ad, tag: "на проверке Яндекса", tagTone: "accent" }
      : ad);
    render(<Ads store={new MockStore(state)} />);

    await userEvent.click(screen.getByRole("button", { name: "На проверке Яндекса 1" }));

    expect(screen.getByRole("button", { name: /Открыть объявление «Маникюр от 1/ })).toHaveTextContent("На проверке Яндекса");
    expect(screen.queryByRole("button", { name: "Открыть объявление «Педикюр рядом с домом»" })).toBeNull();
  });

  it("показывает сообщения при пустых списках фильтров и открывает AdMaker", async () => {
    // filter "off" когда нет отключённых
    const store = new MockStore();
    render(<Ads store={store} />);
    await userEvent.click(screen.getByRole("button", { name: "Отключены 0" }));
    expect(screen.getByText("Отключённых объявлений нет.")).toBeInTheDocument();

    // filter "on" когда все отключены
    cleanup();
    const storeAllOff = new MockStore({ ...initialState(), adsOff: { a1: true, a2: true, a3: true } });
    render(<Ads store={storeAllOff} />);
    await userEvent.click(screen.getByRole("button", { name: "Работают 0" }));
    expect(screen.getByText("Сейчас ни одно объявление не показывается.")).toBeInTheDocument();

    // Открытие создания нового объявления
    await userEvent.click(screen.getByRole("button", { name: "Все 3" }));
    await userEvent.click(screen.getByRole("button", { name: "Новое объявление" }));
    expect(screen.getByRole("heading", { level: 2, name: "Новое объявление" })).toBeInTheDocument();
  });
});
