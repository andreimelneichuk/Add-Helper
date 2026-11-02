import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { rub } from "../data";
import { initialState, Store } from "../store";
import { Ads } from "./Ads";
import { Settings } from "./Settings";

describe("экран рекламы", () => {
  it("свой бюджет применяется кнопкой, пишется в историю, меньше минимума нельзя", async () => {
    const store = new Store();
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
    const store = new Store();
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
    const store = new Store();
    const before = store.state.hist.length;
    render(<Ads store={store} />);

    await userEvent.click(screen.getByRole("button", { name: "Отключить «Педикюр рядом с домом»" }));
    expect(store.state.adsOff.a3).toBeFalsy();
    act(() => store.confirm());

    expect(store.state.adsOff.a3).toBe(true);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(store.state.hist[0]?.title).toBe("Отключил «Педикюр рядом с домом»");
    expect(screen.getByText("Отключено")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Включить «Педикюр рядом с домом»" }));

    expect(store.state.adsOff.a3).toBe(false);
    expect(store.state.hist).toHaveLength(before + 2);
    expect(store.state.hist[0]?.title).toBe("Включил «Педикюр рядом с домом»");
  });

  it("показывает показатели объявления с верным окончанием", () => {
    render(<Ads store={new Store()} />);
    expect(screen.getByText(`7 обращений, по ${rub(2300 / 7)}`)).toBeInTheDocument();
    expect(screen.getByText(`2 обращения, по ${rub(590)}`)).toBeInTheDocument();
  });

  it("в первые дни не приписывает объявлениям накопленные обращения", () => {
    render(<Ads store={new Store(initialState("work", "first"))} />);
    expect(screen.getAllByText("Собираю данные")).toHaveLength(3);
    expect(screen.queryByText(/7 обращений/)).toBeNull();
  });

  it("новая цена видна в объявлении и в профиле", () => {
    const store = new Store();
    store.run({ action: "price", value: 1400 });
    render(<Ads store={store} />);
    const name = `Маникюр от ${rub(1400)}`;
    expect(screen.getAllByText(name)).toHaveLength(2);
    expect(screen.getByRole("button", { name: `Отключить «${name}»` })).toBeInTheDocument();
    expect(screen.queryByText("Маникюр от 1 200 ₽")).toBeNull();
    cleanup();
    render(<Settings store={store} />);
    expect(screen.getByText(/маникюр от 1 400 ₽, педикюр от 1 800 ₽/)).toBeInTheDocument();
  });

  it("кнопка «Что-то изменилось» открывает чат с готовой фразой", async () => {
    const store = new Store();
    render(<Settings store={store} />);
    await userEvent.click(screen.getByRole("button", { name: "Что-то изменилось" }));
    expect(store.state.mode).toBe("chat");
    expect(store.state.input).toBe("Новая цена на маникюр 1400");
  });

  it("настроек на экране рекламы нет, а обводка есть у кнопки «Открыть»", () => {
    render(<Ads store={new Store()} />);
    expect(screen.queryByRole("group", { name: "Бюджет в месяц" })).toBeNull();
    expect(screen.getByRole("button", { name: "Открыть «Первый визит со скидкой»" })).toHaveClass("ah-btn");
    expect(screen.getByRole("button", { name: "Открыть «Первый визит со скидкой»" })).not.toHaveClass("ah-btn-text");
  });

  it("список можно сузить до отключённых, счётчик совпадает", async () => {
    const store = new Store({ ...initialState(), adsOff: { a3: true } });
    render(<Ads store={store} />);
    await userEvent.click(screen.getByRole("button", { name: "Отключены 1" }));
    expect(screen.getByRole("button", { name: "Включить «Педикюр рядом с домом»" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть «Первый визит со скидкой»" })).toBeNull();
  });
});
