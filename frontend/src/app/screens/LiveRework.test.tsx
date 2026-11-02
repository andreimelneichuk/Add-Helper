import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReworkView } from "../../api";
import type { Ad } from "../data";
import type { ReworkApi } from "../store";
import { LiveRework } from "./AdDetail";

const AD: Ad = { id: "anna-direct/11", name: "Маникюр", tag: "", tagTone: "neutral" as Ad["tagTone"], leads: 0, cost: 0, hook: "Маникюр 1500 ₽", title: "Маникюр 1500 ₽", text: "Стерильные инструменты.", facts: "" };
const variant = (id: number, headline: string) => ({ id, headline, text: "Ждём вас: ул. Ленина, 5.", state: "ready", sources: [{ fragment: "Ленина, 5", label: "Адрес", key: "address" }] });

function fakeApi(views: ReworkView[], refusal?: string): ReworkApi & { calls: unknown[][] } {
  const calls: unknown[][] = [];
  let i = 0;
  return {
    calls,
    rework: vi.fn(async (...args: unknown[]) => {
      calls.push(["rework", ...args]);
      if (refusal) throw new Error(refusal);
      return { id: 7, status: "queued" };
    }),
    reworkSet: vi.fn(async () => views[Math.min(i++, views.length - 1)]!),
    chooseRework: vi.fn(async (...args: unknown[]) => {
      calls.push(["choose", ...args]);
      return { ...views[views.length - 1]!, text: "Поставил в показы. Пока Яндекс проверяет новое, старое объявление работает." };
    }),
  } as unknown as ReworkApi & { calls: unknown[][] };
}

const queued: ReworkView = { id: 7, status: "queued", ad_id: "11", problem: null, variants: [] };
const ready: ReworkView = { id: 7, status: "ready", ad_id: "11", problem: null, variants: [variant(21, "Маникюр за 1500 ₽"), variant(22, "Маникюр на Ленина, 5")] };

describe("переделка на сервисе", () => {
  it("ждёт варианты, показывает их рядом со старым и ставит выбранный", async () => {
    const api = fakeApi([queued, ready]);
    const onDone = vi.fn();
    render(<LiveRework api={api} ad={AD} hook={AD.hook} title={AD.title} waitMs={0} onDone={onDone} onCancel={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Короче" }));
    expect(await screen.findByText("Вариант 2")).toBeInTheDocument();
    expect(screen.getByText("Сейчас в показах")).toBeInTheDocument();
    expect(api.calls[0]).toEqual(["rework", "anna-direct", "11", "Короче"]);
    await userEvent.click(screen.getAllByRole("radio")[1]!);
    await userEvent.click(screen.getByRole("button", { name: "Поставить в показы" }));
    expect(api.calls[1]).toEqual(["choose", 7, 22]);
    expect(onDone).toHaveBeenCalledWith(expect.stringContaining("старое объявление работает"));
  });

  it("отказ сервиса виден владельцу, ставить нечего", async () => {
    const api = fakeApi([queued], "В профиле нет «30». Чтобы это было в рекламе, сначала добавьте это в профиль.");
    render(<LiveRework api={api} ad={AD} hook={AD.hook} title={AD.title} waitMs={0} onDone={() => {}} onCancel={() => {}} />);
    await userEvent.type(screen.getByLabelText("Или напишите своими словами"), "Скидка 30%");
    await userEvent.click(screen.getByRole("button", { name: "Сделать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("сначала добавьте это в профиль");
    expect(screen.getByRole("button", { name: "Поставить в показы" })).toBeDisabled();
  });

  it("когда писатель не справился, так и говорит", async () => {
    const api = fakeApi([{ ...queued, status: "failed", problem: "Писатель не ответил. Старое объявление работает." }]);
    render(<LiveRework api={api} ad={AD} hook={AD.hook} title={AD.title} waitMs={0} onDone={() => {}} onCancel={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Упор на адрес" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("не ответил");
  });
});
