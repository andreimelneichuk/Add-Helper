import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { LaunchAnswer, LaunchCheck } from "../../../api";
import { MockStore } from "../../../mock/MockStore";
import { initialState } from "../../store";
import { LaunchModal } from "./LaunchModal";

describe("Модальное окно запуска рекламы (LaunchModal)", () => {
  const readyCheck: LaunchCheck = {
    items: [
      { key: "account", ok: true, text: "Кабинет Директа подключён: stand-direct-1" },
      { key: "marking", ok: true, text: "Реквизиты для маркировки указаны" },
      { key: "ads", ok: true, text: "Объявления готовы к запуску (5)" },
    ],
    ready: true,
    launch_available: true,
    launch_text: "Бюджет 18 000 ₽ в неделю.",
  };

  const answer: LaunchAnswer = {
    status: "done",
    campaigns: [{ name: "Поиск и сети (ah-1-1)", limit_rubles: 18000 }],
    text: "Кампании успешно созданы.",
  };

  it("не рендерится, когда isOpen=false", () => {
    const store = new MockStore(initialState("work"));
    const { container } = render(<LaunchModal isOpen={false} onClose={vi.fn()} store={store} />);
    expect(container.firstChild).toBeNull();
  });

  it("проверяет готовность и запускает рекламу", async () => {
    const user = userEvent.setup();
    const store = new MockStore(initialState("work"));
    const launchCheck = vi.fn().mockResolvedValue(readyCheck);
    const startLaunch = vi.fn().mockResolvedValue(answer);

    (store as any).api = {
      launchCheck,
      startLaunch,
    };

    render(<LaunchModal isOpen={true} onClose={vi.fn()} store={store} />);

    expect(screen.getByRole("status")).toHaveTextContent(/Проверяю готовность/);

    await waitFor(() => {
      expect(screen.getByText(/Кабинет Директа подключён/)).toBeInTheDocument();
    });

    expect(screen.getByText("Бюджет 18 000 ₽ в неделю.")).toBeInTheDocument();

    const launchBtn = screen.getByRole("button", { name: "Запустить рекламу" });
    expect(launchBtn).toBeEnabled();

    await user.click(launchBtn);

    await waitFor(() => {
      expect(screen.getByText("Кампании успешно созданы.")).toBeInTheDocument();
    });
    expect(screen.getByText("Поиск и сети (ah-1-1)")).toBeInTheDocument();
  });

  it("при неготовности показывает предупреждения и кнопку перехода в настройки", async () => {
    const user = userEvent.setup();
    const store = new MockStore(initialState("work"));
    const notReadyCheck: LaunchCheck = {
      items: [
        { key: "account", ok: false, text: "Кабинет Директа не подключён" },
      ],
      ready: false,
      launch_available: false,
      launch_text: "",
    };

    (store as any).api = {
      launchCheck: vi.fn().mockResolvedValue(notReadyCheck),
      startLaunch: vi.fn(),
    };

    const onClose = vi.fn();
    render(<LaunchModal isOpen={true} onClose={onClose} store={store} />);

    await waitFor(() => {
      expect(screen.getByText("Кабинет Директа не подключён")).toBeInTheDocument();
    });

    const settingsBtn = screen.getByRole("button", { name: "Перейти в настройки" });
    expect(settingsBtn).toBeInTheDocument();

    await user.click(settingsBtn);
    expect(onClose).toHaveBeenCalled();
    expect(store.state.view).toBe("settings");
  });

  it("закрывается при нажатии клавиши Escape", async () => {
    const user = userEvent.setup();
    const store = new MockStore(initialState("work"));
    const onClose = vi.fn();
    (store as any).api = {
      launchCheck: vi.fn().mockReturnValue(new Promise(() => {})),
      startLaunch: vi.fn(),
    };

    render(<LaunchModal isOpen={true} onClose={onClose} store={store} />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("закрывается при клике на оверлей (backdrop)", async () => {
    const user = userEvent.setup();
    const store = new MockStore(initialState("work"));
    const onClose = vi.fn();
    (store as any).api = {
      launchCheck: vi.fn().mockReturnValue(new Promise(() => {})),
      startLaunch: vi.fn(),
    };

    const { container } = render(<LaunchModal isOpen={true} onClose={onClose} store={store} />);
    const overlay = container.querySelector(".ah-modal-overlay");
    expect(overlay).not.toBeNull();
    if (overlay) {
      await user.click(overlay);
      expect(onClose).toHaveBeenCalledTimes(1);
    }
  });

  it("выводит сообщение об ошибке, если api недоступен", async () => {
    const store = new MockStore(initialState("work"));
    render(<LaunchModal isOpen={true} onClose={vi.fn()} store={store} />);

    await waitFor(() => {
      expect(
        screen.getByText("API сервиса недоступен в демонстрационном режиме.")
      ).toBeInTheDocument();
    });
  });
});
