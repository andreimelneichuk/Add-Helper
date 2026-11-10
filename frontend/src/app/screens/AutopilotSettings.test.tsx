// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api, AutopilotView } from "../../api";
import { AutopilotSettings } from "./AutopilotSettings";

const BASE: AutopilotView = {
  mode: "confirm", allowed_actions: ["pause_ad", "start_test"], cadence: "daily", run_hour: 6, envelope: {},
  status: "ok", next_run_at: "2026-11-21T03:20:00+00:00", last_run_at: null, stopped_at: null, auto_allowed: false,
  history: [{ text: "Перевёл автопилот в режим «Только следит»", at: "2026-11-20T09:00:00+00:00" }],
};

function fake(view: AutopilotView = BASE) {
  let current = view;
  const api = {
    autopilot: vi.fn(async () => current),
    saveAutopilot: vi.fn(async (patch: Partial<AutopilotView> & { resume?: boolean }) => {
      current = { ...current, ...patch, stopped_at: patch.resume ? null : current.stopped_at, status: patch.resume ? "ok" : current.status };
      return current;
    }),
    stopAutopilot: vi.fn(async () => (current = { ...current, status: "stopped", stopped_at: "2026-11-20T09:00:00+00:00", next_run_at: null })),
  };
  return { api: api as unknown as Api, mock: api };
}

afterEach(cleanup);

describe("настройки автопилота", () => {
  it("показывает положение, статус и расписание словами владельца", async () => {
    const { api } = fake();
    render(<AutopilotSettings api={api} />);
    expect(await screen.findByText("Автопилот работает")).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Предлагает/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/Следующий разбор/)).toBeTruthy();
    expect((screen.getByLabelText("В какой час разбирать") as HTMLSelectElement).value).toBe("6");
  });

  it("«Действует сам» видно, но нажать нельзя, пока не разрешено", async () => {
    const { api, mock } = fake();
    render(<AutopilotSettings api={api} />);
    const auto = await screen.findByRole("radio", { name: /Действует сам/ });
    expect((auto as HTMLButtonElement).disabled).toBe(true);
    expect(auto.textContent).toContain("ещё набираю данные");
    fireEvent.click(auto);
    expect(mock.saveAutopilot).not.toHaveBeenCalled();
  });

  it("смена положения и расписания уходит на сервис", async () => {
    const { api, mock } = fake();
    render(<AutopilotSettings api={api} />);
    fireEvent.click(await screen.findByRole("radio", { name: /Только следит/ }));
    await waitFor(() => expect(mock.saveAutopilot).toHaveBeenCalledWith({ mode: "watch" }));
    fireEvent.change(screen.getByLabelText("Как часто разбирать"), { target: { value: "weekly" } });
    await waitFor(() => expect(mock.saveAutopilot).toHaveBeenCalledWith({ cadence: "weekly" }));
  });

  it("последнее разрешённое действие снять нельзя", async () => {
    const { api, mock } = fake({ ...BASE, allowed_actions: ["pause_ad"] });
    render(<AutopilotSettings api={api} />);
    fireEvent.click(await screen.findByLabelText(/Отключать объявления/));
    expect(mock.saveAutopilot).not.toHaveBeenCalled();
  });

  it("не открытое владельцем действие отключено и объяснено", async () => {
    const { api } = fake({ ...BASE, granted_actions: ["pause_ad", "start_test"] });
    render(<AutopilotSettings api={api} />);
    const box = (await screen.findByLabelText(/Поднимать лимит/)) as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.checked).toBe(false);
    expect(screen.getByText(/сам смогу позже, это откроет команда сервиса/)).toBeTruthy();
  });

  it("открытый рост лимита честно назван предложением: сам он его пока не делает", async () => {
    const { api } = fake({ ...BASE, granted_actions: ["pause_ad", "start_test", "increase_limit"] });
    render(<AutopilotSettings api={api} />);
    const box = (await screen.findByLabelText(/Поднимать лимит/)) as HTMLInputElement;
    expect(box.disabled).toBe(false);
    expect(screen.getByText(/сам это делать ещё не умею/)).toBeTruthy();
  });

  it("остановка просит подтверждения, потом можно включить снова", async () => {
    const { api, mock } = fake();
    render(<AutopilotSettings api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Остановить автопилот" }));
    expect(mock.stopAutopilot).not.toHaveBeenCalled();
    expect(screen.getByText(/реклама и её лимиты не изменятся/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Да, остановить" }));
    expect(await screen.findByText(/Автопилот остановлен/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Снова включить автопилот" }));
    await waitFor(() => expect(mock.saveAutopilot).toHaveBeenCalledWith({ resume: true }));
  });

  it("при отказе сервиса говорит об этом и не прячет настройки", async () => {
    const { api, mock } = fake();
    mock.saveAutopilot.mockRejectedValueOnce(new Error("Режим «Сам» ещё не разрешён"));
    render(<AutopilotSettings api={api} />);
    fireEvent.click(await screen.findByRole("radio", { name: /Только следит/ }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Предлагает/ })).toBeTruthy();
  });
});
