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
});
