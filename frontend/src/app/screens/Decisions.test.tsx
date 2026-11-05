import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { Api, Board } from "../../api";
import { fakeApi, open } from "../testLive";
import { Ads } from "./Ads";

const creative = (id: number, state: Board["creatives"][number]["state"], headline: string): Board["creatives"][number] => ({
  id, account: "anna", campaign_id: "1", headline, text: "Текст", source: "model", state, reason: "", state_reason: "written",
  sources: [{ fragment: "1 200", label: "цена", key: "price" }], history: [], created_at: "", updated_at: "", campaign_name: "Маникюр",
});

const board = (creatives: Board["creatives"], writing: Board["writing"] = []): Board => ({ creatives, writing });

describe("решения по объявлениям на настоящих данных", () => {
  it("объявление, ждущее владельца, показано первым и запускается кнопкой", async () => {
    let current = board([creative(7, "needs_owner", "Маникюр со скидкой"), creative(8, "reserve", "Маникюр у метро")]);
    const api = fakeApi({
      creatives: vi.fn(async () => current),
      launch: vi.fn(async () => { current = board([creative(7, "launch_pending", "Маникюр со скидкой"), creative(8, "reserve", "Маникюр у метро")]); return {}; }),
    } as unknown as Partial<Api>);
    const store = await open(api);
    render(<Ads store={store} />);
    const decide = await screen.findByRole("region", { name: "Нужно ваше решение" });
    expect(within(decide).getByText("Факты из профиля: цена")).toBeInTheDocument();
    // Решение стоит выше списка идущих объявлений.
    expect(decide.compareDocumentPosition(screen.getByRole("heading", { name: "Объявления" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await userEvent.click(within(decide).getByRole("button", { name: "Запустить" }));

    expect(api.launch).toHaveBeenCalledWith(7);
    expect(store.state.notice).toContain("Разрешил запуск «Маникюр со скидкой»");
    expect(screen.queryByRole("region", { name: "Нужно ваше решение" })).not.toBeInTheDocument();
    const queue = screen.getByRole("region", { name: "Готовлю к запуску" });
    expect(within(queue).getAllByRole("listitem").map((x) => x.textContent)).toEqual([
      expect.stringContaining("уйдёт в Яндекс первым"),
      expect.stringContaining("в запасе на замену"),
    ]);
  });

  it("снять можно и то, что ждёт отправки", async () => {
    const api = fakeApi({ creatives: vi.fn(async () => board([creative(8, "launch_pending", "Маникюр у метро")])) } as unknown as Partial<Api>);
    const store = await open(api);
    render(<Ads store={store} />);
    await userEvent.click(await screen.findByRole("button", { name: "Не запускать «Маникюр у метро»" }));
    expect(api.withdraw).toHaveBeenCalledWith(8);
    expect(store.state.notice).toContain("Не запускаю «Маникюр у метро»");
  });

  it("если объявление уже ушло, говорит об этом и перечитывает", async () => {
    const api = fakeApi({
      creatives: vi.fn(async () => board([creative(7, "needs_owner", "Маникюр со скидкой")])),
      launch: vi.fn(async () => { throw new ApiError(409, "Объявление уже запущено, снято или его нет"); }),
    } as unknown as Partial<Api>);
    render(<Ads store={await open(api)} />);
    await userEvent.click(await screen.findByRole("button", { name: "Запустить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже запущено, снято или его нет");
    expect(vi.mocked(api.creatives).mock.calls.length).toBe(2);
  });

  it("говорит, что пишет новые тексты", async () => {
    const writing = [{ id: 1, account: "anna", campaign_id: "1", reason: "refill", wanted: 1, status: "running", created_at: "", campaign_name: "Маникюр" }];
    render(<Ads store={await open(fakeApi({ creatives: vi.fn(async () => board([], writing)) } as Partial<Api>))} />);
    expect(await screen.findByRole("status", { name: "Что с объявлениями сейчас" })).toHaveTextContent("Пишу новые на освободившееся место «Маникюр».");
  });

  it("кнопка «Не запускать» на карточке решения отклоняет объявление", async () => {
    const api = fakeApi({
      creatives: vi.fn(async () => board([creative(9, "needs_owner", "Новое объявление")])),
      withdraw: vi.fn(async () => ({})),
    } as unknown as Partial<Api>);
    const store = await open(api);
    render(<Ads store={store} />);
    const decide = await screen.findByRole("region", { name: "Нужно ваше решение" });
    await userEvent.click(within(decide).getByRole("button", { name: "Не запускать" }));
    expect(api.withdraw).toHaveBeenCalledWith(9);
    expect(store.state.notice).toContain("Не запускаю «Новое объявление»");
  });

  it("проверяет plural и варианты статуса очереди", async () => {
    // 2 объявления (few), без имени направления и с решением без фактов
    const cr2 = [
      { ...creative(1, "launch_pending", "Объявление 1"), campaign_name: null, campaign_id: "3", sources: [] },
      { ...creative(2, "launch_pending", "Объявление 2"), campaign_name: null, campaign_id: "3", sources: [] },
      { ...creative(3, "needs_owner", "Объявление 3"), campaign_name: null, campaign_id: "3", sources: [] },
    ];
    const api = fakeApi({ creatives: vi.fn(async () => board(cr2)) } as unknown as Partial<Api>);
    render(<Ads store={await open(api)} />);
    const status = await screen.findByRole("status", { name: "Что с объявлениями сейчас" });
    expect(status).toHaveTextContent("2 объявления готовлю к запуску");
    expect(screen.getAllByText("Направление № 3, уйдёт в Яндекс первым").length).toBeGreaterThan(0);
    expect(screen.getByText("В тексте нет фактов из профиля")).toBeInTheDocument();
  });

  it("ошибка загрузки доски решений выводит алерт", async () => {
    const api = fakeApi({ creatives: vi.fn(async () => { throw new ApiError(500, "Сервер креативов упал"); }) } as unknown as Partial<Api>);
    render(<Ads store={await open(api)} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Сервер креативов упал");
  });

  it("проверяет plural для 11 и 21 элементов, неизвестную причину написания и строковую ошибку", async () => {
    // 11 элементов: tail >= 11 && tail <= 14 -> many ("объявлений готовлю")
    const cr11 = Array.from({ length: 11 }, (_, i) => creative(i + 10, "launch_pending", `Объявление ${i}`));
    const writing = [{ id: 99, account: "anna", campaign_id: "1", reason: "unknown_custom_reason", wanted: 1, status: "running", created_at: "", campaign_name: "Маникюр" }];
    const api = fakeApi({
      creatives: vi.fn(async () => board(cr11, writing)),
      withdraw: vi.fn(async () => { throw "network string failure"; }),
    } as unknown as Partial<Api>);
    render(<Ads store={await open(api)} />);
    const status = await screen.findByRole("status", { name: "Что с объявлениями сейчас" });
    expect(status).toHaveTextContent("11 объявлений готовлю к запуску");
    expect(status).toHaveTextContent("пишу новые «Маникюр»");

    // Строковая ошибка в действии
    await userEvent.click(screen.getByRole("button", { name: "Не запускать «Объявление 0»" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Сервис не ответил");
  });
});
