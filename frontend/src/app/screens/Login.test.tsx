import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Login } from "./Login";

describe("Экран входа (Login)", () => {
  it("показывает логотип, название и кнопку входа", () => {
    render(<Login />);
    expect(screen.getByRole("heading", { level: 1, name: "Ad Helper" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Войти через Яндекс" })).toBeInTheDocument();
    expect(screen.getByText("Управление контекстной рекламой без сложных настроек")).toBeInTheDocument();
    // Нет меню или навигации
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("показывает сообщение об ошибке, если вход не удался", () => {
    render(<Login error="Вход не состоялся: доступ в Яндексе не дан." />);
    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent("Вход не состоялся: доступ в Яндексе не дан.");
  });

  it("по клику вызывает переход на вход", async () => {
    const onLogin = vi.fn();
    render(<Login onLogin={onLogin} />);
    const btn = screen.getByRole("button", { name: "Войти через Яндекс" });
    await userEvent.click(btn);
    expect(onLogin).toHaveBeenCalledOnce();
  });

  it("по умолчанию перенаправляет на /api/auth/yandex/login", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<Login />);
    const btn = screen.getByRole("button", { name: "Войти через Яндекс" });
    await userEvent.click(btn);
    expect(assign).toHaveBeenCalledWith("/api/auth/yandex/login");
    vi.unstubAllGlobals();
  });
});
