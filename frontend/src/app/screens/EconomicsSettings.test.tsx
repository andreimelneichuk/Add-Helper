// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api, EconomicsPatch, EconomicsView } from "../../api";
import { EconomicsSettings } from "./EconomicsSettings";

const BASE: EconomicsView = {
  check_rubles: 1750, check_from_profile: true, lead_to_client_bp: 3000, margin_bp: 5000, repeat_bp: 3000, ad_share_bp: 3000,
  capacity_per_week: null, confirmed: false, goal_rubles: 102,
  how: "Средний чек 1 750 ₽ × … = 102 ₽ за обращение.",
  services: [{ service: "Маникюр", price_rubles: 1500, goal_rubles: 88 }],
};

function fake(view: EconomicsView = BASE) {
  const api = {
    economics: vi.fn(async () => view),
    saveEconomics: vi.fn(async (patch: EconomicsPatch) => ({ ...view, ...patch, confirmed: !!patch.confirm })),
  };
  return { api: api as unknown as Api, mock: api };
}

afterEach(cleanup);

describe("сколько стоит клиент", () => {
  it("показывает ориентир, цель и как она посчитана", async () => {
    const { api } = fake();
    render(<EconomicsSettings api={api} />);
    expect(await screen.findByText(/Можно платить за обращение до 102 ₽/)).toBeTruthy();
    expect(screen.getByText(/ориентир для салона/)).toBeTruthy();
    expect(screen.getByText(/Как посчитано: Средний чек 1 750/)).toBeTruthy();
    expect(screen.getByText(/Маникюр \(1500 ₽\): до 88 ₽/)).toBeTruthy();
    expect((screen.getByLabelText("Прибыль с чека, %") as HTMLInputElement).value).toBe("50");
  });

  it("«Это мои цифры» отправляет проценты базисными пунктами с подтверждением", async () => {
    const { api, mock } = fake();
    render(<EconomicsSettings api={api} />);
    const margin = await screen.findByLabelText("Прибыль с чека, %");
    fireEvent.change(margin, { target: { value: "42,5" } });
    fireEvent.click(screen.getByRole("button", { name: "Это мои цифры" }));
    await waitFor(() => expect(mock.saveEconomics).toHaveBeenCalled());
    expect(mock.saveEconomics).toHaveBeenCalledWith({ lead_to_client_bp: 3000, margin_bp: 4250, repeat_bp: 3000, ad_share_bp: 3000, confirm: true });
    expect(await screen.findByText("Цифры подтверждены вами")).toBeTruthy();
  });

  it("не отправляет не-числа и называет, что поправить", async () => {
    const { api, mock } = fake();
    render(<EconomicsSettings api={api} />);
    fireEvent.change(await screen.findByLabelText("Прибыль с чека, %"), { target: { value: "много" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить цифры" }));
    expect((await screen.findByRole("alert")).textContent).toContain("в процентах");
    expect(mock.saveEconomics).not.toHaveBeenCalled();
  });

  it("свой средний чек уходит рублями, чек из профиля без правки не отправляется", async () => {
    const { api, mock } = fake();
    render(<EconomicsSettings api={api} />);
    const check = await screen.findByLabelText("Средний чек, ₽");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить цифры" }));
    await waitFor(() => expect(mock.saveEconomics).toHaveBeenCalledTimes(1));
    expect(mock.saveEconomics.mock.calls[0]?.[0]).not.toHaveProperty("avg_check_rubles");
    fireEvent.change(check, { target: { value: "3000" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить цифры" }));
    await waitFor(() => expect(mock.saveEconomics).toHaveBeenCalledTimes(2));
    expect(mock.saveEconomics.mock.calls[1]?.[0]).toMatchObject({ avg_check_rubles: 3000 });
  });

  it("без цен услуг и чека просит их указать, а не выдумывает цель", async () => {
    const { api } = fake({ ...BASE, check_rubles: null, goal_rubles: null, how: "", services: [] });
    render(<EconomicsSettings api={api} />);
    expect(await screen.findByText(/Укажите средний чек или цены услуг/)).toBeTruthy();
    expect(screen.queryByText(/Можно платить/)).toBeNull();
  });
});

describe("повторное чтение", () => {
  it("не затирает то, что владелец уже вписал", async () => {
    let release: (view: EconomicsView) => void = () => undefined;
    const slow = new Promise<EconomicsView>((resolve) => { release = resolve; });
    const calls = [BASE];
    const api = { economics: vi.fn(async () => (calls.length ? calls.shift()! : slow)), saveEconomics: vi.fn() } as unknown as Api;
    const { rerender } = render(<EconomicsSettings api={api} />);
    const margin = await screen.findByLabelText("Прибыль с чека, %");
    fireEvent.change(margin, { target: { value: "40" } });
    rerender(<EconomicsSettings api={api} />);
    release(BASE);
    await waitFor(() => expect((screen.getByLabelText("Прибыль с чека, %") as HTMLInputElement).value).toBe("40"));
  });
});
