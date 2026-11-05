import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { initialState, Store } from "../store";
import { Chat } from "./Chat";

function open(stage: "work" | "start" = "work") {
  const store = new Store(initialState(stage));
  store.openChat();
  render(<Chat store={store} />);
  return store;
}

function say(text: string) {
  fireEvent.change(screen.getByLabelText("Сообщение для ИИ"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
}

describe("чат", () => {
  it("фраза меняет настройку только после «Применить»", () => {
    const store = open();
    say("Не показывай по воскресеньям");
    expect(store.state.f.hours).toBe("в часы работы");
    expect(screen.getByText(/Понял так: не показывать/)).toBeInTheDocument();
    expect(screen.getByLabelText("Сообщение для ИИ")).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(store.state.f.hours).toBe("кроме воскресений");
    expect(store.state.pending).toBeNull();
    expect(screen.queryByRole("button", { name: "Применить" })).toBeNull();
    expect(screen.getByRole("button", { name: "Как дела?" })).toBeInTheDocument();
  });

  it("бюджет из фразы не меняется без подтверждения", () => {
    const store = open();
    say("Поставь бюджет 25000");
    expect(store.state.f.budget).toBe(20000);
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(store.state.f.budget).toBe(25000);
  });

  it("«Не надо» ничего не меняет", () => {
    const store = open();
    const { f, hist } = store.state;
    say("Поставь бюджет 25000");
    fireEvent.click(screen.getByRole("button", { name: "Не надо" }));
    expect(store.state.f).toEqual(f);
    expect(store.state.hist).toEqual(hist);
    expect(store.state.pending).toBeNull();
    expect(screen.getByText("Хорошо, ничего не меняю.")).toBeInTheDocument();
  });

  it("кнопка в ответе ведёт на экран", () => {
    const store = open();
    say("Покажи историю");
    fireEvent.click(screen.getByRole("button", { name: "Открыть историю" }));
    expect(store.state.mode).toBe("panel");
    expect(store.state.view).toBe("history");
  });

  it("отправляет по Enter через форму и не шлёт пустое", () => {
    const store = open();
    const count = store.state.msgs.length;
    const input = screen.getByLabelText("Сообщение для ИИ");
    fireEvent.submit(input.closest("form")!);
    expect(store.state.msgs).toHaveLength(count);

    fireEvent.change(input, { target: { value: "Как дела?" } });
    fireEvent.submit(input.closest("form")!);
    expect(store.state.msgs).toHaveLength(count + 2);
    expect(input).toHaveFocus();
  });

  it("объявляет сообщения и прокручивает к новому", () => {
    open();
    const log = screen.getByRole("log");
    expect(log).toHaveAttribute("aria-live", "polite");
    Object.defineProperty(log, "scrollHeight", { configurable: true, value: 500 });
    say("Как дела?");
    expect(log.scrollTop).toBe(500);
  });

  it("на этапе запуска свои подсказки и текст поля", () => {
    const store = open("start");
    expect(screen.getByLabelText("Сообщение для ИИ")).toHaveAttribute("placeholder", "Ссылка или пара слов о бизнесе");
    fireEvent.click(screen.getByRole("button", { name: "salon-primer.ru" }));
    expect(store.state.site).toBe("salon-primer.ru");
    expect(store.state.step).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Работаем до 22:00" }));
    expect(store.state.facts.at(-1)?.value).toBe("Работаем до 22:00");
    say("дальше");
    expect(store.state.step).toBe(2);

    fireEvent.click(screen.getByRole("button", { name: "20 000 ₽ в месяц" }));
    expect(store.state.f.budget).toBe(20000);
    expect(screen.getByText("Поставил 20 000 ₽ в месяц.")).toBeInTheDocument();
  });
});
