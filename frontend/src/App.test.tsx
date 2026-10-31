import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { STORAGE_KEY } from "./wizard/state";

const next = () => userEvent.click(screen.getByRole("button", { name: "Дальше" }));

async function toMarking() {
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Войти через Яндекс" }));
  await userEvent.click(screen.getByRole("button", { name: "Подключить кабинет" }));
  await next();
  await userEvent.type(screen.getByLabelText("Название"), "Лак");
  await userEvent.type(screen.getByLabelText(/Главная услуга/), "Маникюр");
  await userEvent.type(screen.getByLabelText(/Цена услуги/), "1500");
  await next();
}

describe("мастер", () => {
  it("без согласия дальше не пускает", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: "Войти через Яндекс" })).toBeDisabled();
    expect(screen.getByText(/Тестовый режим/)).toBeInTheDocument();
  });

  it("проходится целиком до главной и отмены в истории", async () => {
    render(<App />);
    await toMarking();
    await userEvent.type(screen.getByLabelText("Название или ФИО"), "ИП Иванова");
    await userEvent.type(screen.getByLabelText(/^ИНН/), "500100732259");
    await next();
    await userEvent.click(screen.getByRole("button", { name: "Проверить" }));
    await next();
    await next();
    await userEvent.type(screen.getByLabelText("Средний чек, ₽"), "2500");
    expect(screen.getByLabelText(/Цена клиента/)).toHaveValue("750");
    expect(screen.getByText(/грубая оценка/)).toBeInTheDocument();
    await next();
    expect(screen.getByText("Маникюр за 1500 ₽")).toBeInTheDocument();
    await next();
    expect(screen.getByText(/30 000 ₽ в месяц/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Запустить" }));
    await userEvent.click(screen.getByRole("button", { name: "Перейти на главную" }));

    expect(screen.getByRole("heading", { name: "Реклама работает" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Вся история" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Отменить" })[2] as HTMLElement);
    expect(screen.getByText(/оно разделит его с другими/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Да, отменить" }));
    expect(screen.getByText(/Отменено: Отключено объявление/)).toBeInTheDocument();
  });

  it("неверный ИНН останавливает и объясняет", async () => {
    render(<App />);
    await toMarking();
    await userEvent.type(screen.getByLabelText("Название или ФИО"), "ИП Иванова");
    await userEvent.type(screen.getByLabelText(/^ИНН/), "500100732258");
    expect(screen.getByRole("alert")).toHaveTextContent("ошибка");
    expect(screen.getByRole("button", { name: "Дальше" })).toBeDisabled();
  });

  it("после перерыва продолжает с того же шага, а ИНН не хранит", async () => {
    const { unmount } = render(<App />);
    await toMarking();
    await userEvent.type(screen.getByLabelText(/^ИНН/), "500100732259");
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("500100732259");
    unmount();

    render(<App />);
    expect(screen.getByRole("heading", { name: /данные для маркировки/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/^ИНН/)).toHaveValue("");
  });

  it("низкий бюджет честно предупреждает", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ step: "budget", budget: "3000" }));
    render(<App />);
    expect(screen.getByRole("alert")).toHaveTextContent("очень мало");
  });
});
