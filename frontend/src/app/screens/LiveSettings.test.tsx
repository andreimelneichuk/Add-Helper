import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { Api } from "../../api";
import { fakeApi, open, settled } from "../testLive";
import { Settings } from "./Settings";

afterEach(() => vi.unstubAllGlobals());

describe("настройки на настоящих данных", () => {
  it("правка профиля уходит в API и перечитывает данные экранов", async () => {
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const price = await screen.findByLabelText("Цена услуги 1, ₽");
    await userEvent.clear(price);
    await userEvent.type(price, "1400");
    await userEvent.click(screen.getByRole("button", { name: "Добавить услугу" }));
    await userEvent.type(screen.getByLabelText("Услуга 2"), "Педикюр");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Сохранил");
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({ services: [{ name: "Маникюр", price: 1400 }, { name: "Педикюр", price: null }] }));
    // Профиль перечитан вместе с остальными данными: два раза при открытии экрана и загрузке, ещё раз после сохранения.
    expect(vi.mocked(api.profile).mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("пустую услугу не сохраняет, отказ сервиса показывает как есть", async () => {
    const api = fakeApi({ saveProfile: vi.fn(async () => { throw new ApiError(422, "Слишком длинный адрес"); }) } as Partial<Api>);
    render(<Settings store={await open(api)} />);
    await userEvent.click(await screen.findByRole("button", { name: "Добавить услугу" }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Слишком длинный адрес");
    expect(api.saveProfile).toHaveBeenCalledWith(expect.objectContaining({ services: [{ name: "Маникюр", price: 1200 }] }));
  });

  it("показывает кабинет и ведёт на подключение в Яндекс", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const account = screen.getByRole("region", { name: "Кабинет Яндекса" });
    expect(await within(account).findByText("anna")).toBeInTheDocument();
    expect(within(account).getByText("Подключён")).toBeInTheDocument();
    await userEvent.click(within(account).getByRole("button", { name: "Подключить заново" }));
    expect(assign).toHaveBeenCalledWith("https://oauth.yandex.ru/authorize?x=1");
  });

  it("без кабинета объясняет, зачем он нужен", async () => {
    render(<Settings store={await open(fakeApi({ accounts: vi.fn(async () => []) } as Partial<Api>))} />);
    expect(await screen.findByText(/без него реклама не запустится/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подключить кабинет" })).toBeInTheDocument();
  });

  it("подключает Telegram и проверяет привязку", async () => {
    const opened = vi.fn();
    vi.stubGlobal("open", opened);
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const channels = screen.getByRole("region", { name: "Уведомления" });
    expect(await within(channels).findByText("anna@example.ru")).toBeInTheDocument();
    await userEvent.click(within(channels).getByRole("button", { name: "Подключить" }));
    expect(api.linkTelegram).toHaveBeenCalledOnce();
    expect(opened).toHaveBeenCalledWith("https://t.me/bot?start=x", "_blank", "noopener");
    expect(within(channels).getByText(/Откройте бота/)).toBeInTheDocument();
  });

  it("выход закрывает сессию на сервере", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(api.logout).toHaveBeenCalledOnce();
    expect(assign).toHaveBeenCalledWith("/");
  });
});

describe("бюджет на настоящих данных", () => {
  it("снижение спрашивает подтверждение и объясняет порядок, затем уходит в сервис", async () => {
    const api = fakeApi({ setBudget: vi.fn(async () => ({ status: "applied", cuts: 1 })) } as Partial<Api>);
    const store = await open(api);
    render(<Settings store={store} />);
    const input = screen.getByLabelText("Бюджет в месяц, ₽");
    await userEvent.clear(input);
    await userEvent.type(input, "15000");
    await userEvent.click(screen.getByRole("button", { name: "Изменить бюджет" }));
    expect(api.setBudget).not.toHaveBeenCalled();
    const confirm = screen.getByRole("group", { name: "Подтверждение бюджета" });
    expect(confirm).toHaveTextContent("Сначала снижу лимиты в Яндексе");
    await userEvent.click(within(confirm).getByRole("button", { name: "Применить" }));
    await settled();
    expect(api.setBudget).toHaveBeenCalledWith(15000, expect.any(String));
    expect(store.state.notice).toContain("Бюджет месяца теперь 15 000 ₽");
  });

  it("ниже потраченного не даёт отправить", async () => {
    const api = fakeApi();
    render(<Settings store={await open(api)} />);
    const input = screen.getByLabelText("Бюджет в месяц, ₽");
    await userEvent.clear(input);
    await userEvent.type(input, "1000");
    expect(screen.getByRole("alert")).toHaveTextContent("уже потрачено 1 500 ₽");
    expect(screen.getByRole("button", { name: "Изменить бюджет" })).toBeDisabled();
  });
});
