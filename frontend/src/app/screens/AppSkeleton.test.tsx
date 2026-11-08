import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppSkeleton } from "./AppSkeleton";

describe("Скелетон загрузки (AppSkeleton)", () => {
  it("рендерит каркас со статусом загрузки и доступным описанием", () => {
    render(<AppSkeleton />);
    const root = screen.getByRole("status");
    expect(root).toBeInTheDocument();
    expect(root).toHaveAttribute("aria-label", "Загружаем данные…");
  });

  it("рендерит разделы навигации в отключённом состоянии", () => {
    render(<AppSkeleton />);
    const navs = screen.getAllByRole("navigation");
    expect(navs.length).toBeGreaterThanOrEqual(1);

    const buttons = screen.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(0);
    // Все кнопки в скелетоне неактивны
    for (const btn of buttons) {
      expect(btn).toBeDisabled();
    }
  });

  it("содержит мерцающие плейсхолдеры для ключевых блоков", () => {
    const { container } = render(<AppSkeleton />);
    const shimmers = container.querySelectorAll(".ah-skeleton-shimmer");
    expect(shimmers.length).toBeGreaterThanOrEqual(10);
  });
});
