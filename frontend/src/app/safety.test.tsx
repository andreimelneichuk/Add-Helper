import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";
import { rub } from "./data";
import { Ads } from "./screens/Ads";
import { Overview } from "./screens/Overview";
import { initialState, Store } from "./store";

const button = (name: string | RegExp) => screen.getByRole("button", { name });

describe("изменения с панели", () => {
  it("настройка применяется только после подтверждения", async () => {
    const store = new Store();
    render(<AppShell store={store} />);
    await userEvent.click(button("Настройки"));
    await userEvent.click(button("вся Москва"));
    expect(store.state.f.area).toBe("Сокольники");
    expect(screen.getByRole("group", { name: "Подтверждение" })).toHaveTextContent("вся Москва");
    await userEvent.click(button("Не надо"));
    expect(store.state.f.area).toBe("Сокольники");
    await userEvent.click(button("вся Москва"));
    await userEvent.click(button("Применить"));
    expect(store.state.f.area).toBe("вся Москва");
  });

  it("свою настройку можно отменить из истории, значение возвращается", () => {
    const store = new Store();
    store.run({ action: "setting", value: ["area", "вся Москва"] });
    const entry = store.state.hist[0]!;
    expect(entry.canUndo).toBe(true);
    store.run({ action: "undo", value: entry.id });
    expect(store.state.f.area).toBe("Сокольники");
  });

  it("после новой правки той же настройки старую запись не отменить", () => {
    const store = new Store();
    store.run({ action: "setting", value: ["area", "вся Москва"] });
    const old = store.state.hist[0]!.id;
    store.run({ action: "setting", value: ["area", "Сокольники и Преображенка"] });
    expect(store.state.hist.find((h) => h.id === old)?.canUndo).toBe(false);
  });

  it("бюджет ниже потраченного не принимается ни с панели, ни из чата", () => {
    const store = new Store();
    store.ask({ action: "setting", value: ["budget", 1000] });
    expect(store.state.pending).toBeNull();
    expect(store.state.notice).toContain(rub(8400));
    store.send("бюджет 5000");
    expect(store.state.pending).toBeNull();
    expect(store.state.msgs.at(-1)?.text).toContain(rub(8400));
    expect(store.state.f.budget).toBe(20000);
  });

  it("последнее объявление не отключается, вместо этого предлагается пауза", () => {
    const store = new Store(initialState());
    store.set({ adsOff: { a1: true, a2: true } });
    store.ask({ action: "ad", value: "a3" });
    expect(store.state.pending).toBeNull();
    expect(store.state.adsOff.a3).toBeFalsy();
    expect(store.state.notice).toContain("Пауза");
  });

  it("отключение объявления в проверке предупреждает о её остановке и отменяется", async () => {
    const store = new Store();
    render(<AppShell store={store} />);
    act(() => store.goView("ads"));
    await userEvent.click(button("Отключить «Первый визит со скидкой»"));
    expect(store.state.adsOff.a1).toBeFalsy();
    expect(screen.getByRole("group", { name: "Подтверждение" })).toHaveTextContent(/проверк/i);
    await userEvent.click(button("Применить"));
    expect(store.state.adsOff.a1).toBe(true);
    act(() => store.run({ action: "undo", value: store.state.hist[0]!.id }));
    expect(store.state.adsOff.a1).toBe(false);
  });

  it("включение объявления подтверждения не требует", async () => {
    const store = new Store({ ...initialState(), adsOff: { a3: true } });
    render(<Ads store={store} />);
    await userEvent.click(button("Включить «Педикюр рядом с домом»"));
    expect(store.state.adsOff.a3).toBe(false);
  });
});

describe("обзор отвечает на вопрос «всё ли в порядке»", () => {
  it("в обычной ситуации первым идёт вывод с цифрами", () => {
    render(<Overview store={new Store()} />);
    expect(screen.getByRole("heading", { level: 2, name: `Всё в порядке: 12 обращений за 7 дней, по ${rub(420)}` })).toBeInTheDocument();
  });

  it("на паузе вывод не говорит, что всё в порядке", () => {
    const store = new Store();
    store.run({ action: "pause" });
    render(<Overview store={store} />);
    expect(within(screen.getByRole("status")).getByRole("heading", { name: "Реклама на паузе" })).toBeInTheDocument();
    expect(screen.queryByText(/Всё в порядке/)).toBeNull();
  });

  it("цена выше цели не подсвечена как хорошая", () => {
    render(<Overview store={new Store()} />);
    expect(screen.getByText("было 560 ₽, цель 400 ₽")).not.toHaveClass("ov-ink-good");
  });

  it("вопрос о клиентах стоит раньше показателей и подсказывает, где взять ответ", () => {
    const { container } = render(<Overview store={new Store()} />);
    const question = screen.getByLabelText(/стали клиентами/);
    const kpis = container.querySelector(".ov-kpis")!;
    expect(question.compareDocumentPosition(kpis) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/журнал записи/)).toBeInTheDocument();
  });

  it("слишком большое число объясняет ошибку", async () => {
    render(<Overview store={new Store()} />);
    await userEvent.type(screen.getByLabelText(/стали клиентами/), "13");
    await userEvent.click(button("Записать"));
    expect(screen.getByRole("alert")).toHaveTextContent("не больше 12");
  });
});

describe("оболочка", () => {
  it("сумма не переносится по частям", async () => {
    const data = await vi.importActual<typeof import("./data")>("./data");
    expect(data.rub(11600)).toBe("11 600 ₽");
  });

  it("смена раздела открывает его с начала", () => {
    const store = new Store();
    const { container } = render(<AppShell store={store} />);
    const main = container.querySelector(".ah-main")!;
    main.scrollTop = 600;
    act(() => store.goView("ads"));
    expect(main.scrollTop).toBe(0);
  });

  it("раздел называется одинаково на компьютере и телефоне", () => {
    render(<AppShell store={new Store()} />);
    expect(screen.queryByRole("button", { name: "Цифры" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Статистика" })).toHaveLength(2);
  });
});
