import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as apiModule from "../../../api";
import { initialState, Store } from "../../store";
import { GenerateAdsModal } from "./GenerateAdsModal";

describe("GenerateAdsModal", () => {
  it("не рендерится, если isOpen false", () => {
    const store = new Store(initialState("work"));
    render(<GenerateAdsModal isOpen={false} onClose={vi.fn()} store={store} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("рендерится с заголовком и выбором услуг", () => {
    const store = new Store(initialState("work"));
    render(<GenerateAdsModal isOpen={true} onClose={vi.fn()} store={store} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Сгенерировать новые объявления")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Например: Сделай акцент на скидке 20%/)).toBeInTheDocument();
  });

  it("позволяет выбрать свою услугу и ввести текст пожеланий", async () => {
    const store = new Store(initialState("work"));
    render(<GenerateAdsModal isOpen={true} onClose={vi.fn()} store={store} />);

    await userEvent.click(screen.getByText("+ Своя услуга"));
    expect(screen.getByPlaceholderText(/Пилинг лица/)).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText(/Пилинг лица/), "Пилинг лица");
    await userEvent.type(screen.getByPlaceholderText(/Например: Сделай акцент/), "Акция 10% в ноябре");

    expect(screen.getByRole("button", { name: "Сгенерировать через ИИ" })).toBeInTheDocument();
  });

  it("генерирует варианты и позволяет добавить их в показы", async () => {
    vi.spyOn(apiModule, "createGuestApi").mockReturnValue({
      writeAds: vi.fn(async () => ({ id: 1, token: "t", status: "queued" })),
      adSet: vi.fn(async () => ({ id: 1, status: "ready", ads: [{ headline: "Маникюр", text: "Запишитесь онлайн.", facts: ["из профиля"] }] })),
    } as unknown as ReturnType<typeof apiModule.createGuestApi>);
    const store = new Store(initialState("work"));
    const onClose = vi.fn();
    render(<GenerateAdsModal isOpen={true} onClose={onClose} store={store} />);

    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать через ИИ" }));

    // Варианты сгенерировались
    await waitFor(() => {
      expect(screen.getByText(/Вот варианты, которые написал ИИ/)).toBeInTheDocument();
    });

    const addBtn = screen.getByRole("button", { name: /Добавить выбранные/ });
    expect(addBtn).toBeInTheDocument();

    await userEvent.click(addBtn);
    expect(onClose).toHaveBeenCalled();
    expect(store.state.notice).toContain("в запуск");
  });

  it("при отказе писателя показывает причину и не подставляет шаблон", async () => {
    vi.spyOn(apiModule, "createGuestApi").mockReturnValue({
      writeAds: vi.fn(async () => { throw new Error("Писатель не отвечает"); }),
    } as unknown as ReturnType<typeof apiModule.createGuestApi>);
    render(<GenerateAdsModal isOpen={true} onClose={vi.fn()} store={new Store(initialState("work"))} />);
    await userEvent.click(screen.getByRole("button", { name: "Сгенерировать через ИИ" }));
    expect(await screen.findByText("Писатель не отвечает")).toBeInTheDocument();
    expect(screen.queryByText(/Вот варианты/)).toBeNull();
  });
});
