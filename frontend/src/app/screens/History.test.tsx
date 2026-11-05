import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { initialState, Store } from "../store";
import { History } from "./History";

const row = (title: RegExp) => screen.getByRole("article", { name: title });
const OFF = /^Отключил «Маникюр с покрытием/;

describe("история", () => {
  it("отмена требует подтверждения и сохраняет исходную запись", () => {
    const store = new Store();
    render(<History store={store} />);
    const before = store.state.hist.length;

    fireEvent.click(within(row(OFF)).getByRole("button", { name: "Отменить" }));
    expect(store.state.undone.off).toBeUndefined();
    expect(store.state.hist).toHaveLength(before);
    expect(within(row(OFF)).getByText(/Объявление снова включится/)).toBeInTheDocument();
    expect(within(row(OFF)).getByRole("button", { name: "Оставить" })).toHaveFocus();

    fireEvent.click(within(row(OFF)).getByRole("button", { name: "Да, отменить" }));
    expect(store.state.undone.off).toBe(true);
    expect(store.state.hist).toHaveLength(before + 1);
    expect(within(row(OFF)).getByText("Отменено вами")).toBeInTheDocument();
    expect(within(row(OFF)).queryByRole("button")).toBeNull();
    expect(screen.getByRole("article", { name: /^Отменил: Отключил/ })).toBeInTheDocument();
  });

  it("«Оставить» ничего не отменяет и прячет описание", () => {
    const store = new Store();
    render(<History store={store} />);
    const before = store.state.hist.length;

    fireEvent.click(within(row(OFF)).getByRole("button", { name: "Отменить" }));
    fireEvent.click(within(row(OFF)).getByRole("button", { name: "Оставить" }));

    expect(store.state.undone.off).toBeUndefined();
    expect(store.state.hist).toHaveLength(before);
    expect(screen.queryByText(/Объявление снова включится/)).toBeNull();
    expect(within(row(OFF)).getByRole("button", { name: "Отменить" })).toHaveFocus();
  });

  it("для неотменяемой записи показывает причину вместо кнопки", () => {
    render(<History store={new Store()} />);
    const test = within(row(/^Проверка закончена/));
    expect(test.getByText("Можно проверить заново")).toBeInTheDocument();
    expect(test.queryByRole("button")).toBeNull();
  });

  it("запись содержит что сделано, почему и что дальше", () => {
    render(<History store={new Store()} />);
    const line = within(row(/^Передал 3\s000/));
    expect(line.getByText(/Обращения по нему стоят 329 ₽.*Общий бюджет тот же\./)).toBeInTheDocument();
  });

  it("фильтр оставляет только записи своего типа", () => {
    const store = new Store();
    render(<History store={store} />);
    expect(screen.getAllByRole("article")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "Деньги" }));
    expect(screen.getByRole("button", { name: "Деньги" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(row(/^Передал 3\s000/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Все" }));
    expect(screen.getAllByRole("article")).toHaveLength(4);
  });

  it("говорит, когда по фильтру записей нет", () => {
    render(<History store={new Store(initialState("work", "first"))} />);
    fireEvent.click(screen.getByRole("button", { name: "Деньги" }));
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.getByText("Таких записей пока нет.")).toBeInTheDocument();
  });

  it("обновляется при отмене из другого места", () => {
    const store = new Store();
    render(<History store={store} />);
    act(() => store.run({ action: "undo", value: "move" }));
    expect(within(row(/^Передал 3\s000/)).getByText("Отменено вами")).toBeInTheDocument();
  });
});
