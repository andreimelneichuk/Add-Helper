import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Api, AutopilotReport as Report, HistoryLine } from "../../../api";
import { change, fakeApi, open, settled } from "../../testLive";
import { History } from "../History";
import { AutopilotReport } from "./AutopilotReport";

const empty: Report = { since: "2026-11-09T10:00:00Z", done: 0, better: 0, same: 0, worse: 0, insufficient: 0, rolled_back: 0, outcomes: [] };
const withReport = (r: Report) => fakeApi({ autopilotReport: vi.fn(async () => r) } as unknown as Partial<Api>);

describe("карточка «Что автопилот сделал за сутки»", () => {
  it("без действий говорит об этом, а не рисует нули", async () => {
    const store = await open(withReport(empty));
    render(<AutopilotReport store={store} />);
    expect(await screen.findByText(/автопилот ничего не менял/)).toBeInTheDocument();
  });

  it("при малых данных честно пишет «данных мало»", async () => {
    const store = await open(withReport({ ...empty, done: 2, insufficient: 2 }));
    render(<AutopilotReport store={store} />);
    expect(await screen.findByText(/Сделано: 2 действия/)).toBeInTheDocument();
    expect(screen.getByText(/данных мало, оценю позже/)).toBeInTheDocument();
  });

  it("называет автооткат с цифрой, которую посчитал сервис", async () => {
    const outcome = { change_id: 9, object_ref: "ad:11", verdict: "worse", cpl_change_bp: 3000, noise_bp: 500, rolled_back: true, note: null, evaluated_at: "2026-11-10T01:00:00Z" };
    const store = await open(withReport({ ...empty, done: 1, worse: 1, rolled_back: 1, outcomes: [outcome] }));
    render(<AutopilotReport store={store} />);
    expect(await screen.findByText(/Вернул назад сам: 1/)).toBeInTheDocument();
    expect(screen.getByText(/«Маникюр»: стало хуже, цена обращения \+30%, возвращено в показ/)).toBeInTheDocument();
  });

  it("не падает, если сервис отчёта не отдаёт", async () => {
    const store = await open(fakeApi({ autopilotReport: vi.fn(async () => { throw new Error("нет"); }) } as unknown as Partial<Api>));
    const { container } = render(<AutopilotReport store={store} />);
    await settled();
    expect(container).toBeEmptyDOMElement();
  });

  it("кнопка открывает историю с фильтром «Автопилот»", async () => {
    const store = await open(withReport({ ...empty, done: 1, better: 1 }));
    render(<AutopilotReport store={store} />);
    await userEvent.click(await screen.findByRole("button", { name: "Посмотреть и отменить" }));
    expect(store.state.view).toBe("history");
    expect(store.state.filter).toBe("autopilot");
  });
});

const autoLine: HistoryLine = { ...change, ref: "change-7", source: "autopilot", kind: "pause_ad", explanation: "Остановил объявление: цена обращения выше цели." };
const autoUndo: HistoryLine = { ...change, ref: "change-8", source: "rollback", kind: "resume_ad", explanation: "Вернули объявление в показ: после его остановки цена обращения выросла на 30%. Остановку можно сделать снова вручную." };
const ownUndo: HistoryLine = { ...change, ref: "change-9", source: "rollback", kind: "resume_ad", explanation: "Вернул: вы отменили." };

describe("история: фильтр «Автопилот»", () => {
  it("показывает действия автопилота и его автооткаты, но не отмену владельца", async () => {
    const store = await open(fakeApi({ history: vi.fn(async () => [autoLine, autoUndo, ownUndo, change]) } as Partial<Api>));
    expect(store.state.hist.map((h) => !!h.auto)).toEqual([true, true, false, false]);
    expect(store.state.hist[1]).toMatchObject({ mine: false, canUndo: false });
    expect(store.state.hist[1]?.title).toMatch(/^Автопилот вернул/);
    store.set({ filter: "autopilot" });
    render(<History store={store} />);
    expect(screen.getByText(/Автопилот вернул/)).toBeInTheDocument();
    expect(screen.getByText(/цена обращения выросла на 30%/)).toBeInTheDocument();
    expect(screen.queryByText(/вы отменили/)).not.toBeInTheDocument();
  });
});
