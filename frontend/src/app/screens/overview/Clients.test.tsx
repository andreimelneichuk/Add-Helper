import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../api";
import type { Api } from "../../../api";
import { fakeApi, open, overview, settled } from "../../testLive";
import type { Store } from "../../store";
import { useStoreState } from "../../store";
import { Clients } from "./Clients";

function Live({ store }: { store: Store }) {
  return <Clients s={useStoreState(store)} store={store} />;
}

describe("вопрос о клиентах на настоящих данных", () => {
  it("ответ уходит в сервис за окно недели и подставляется после перечитывания", async () => {
    let answered: unknown = null;
    const api = fakeApi({
      overview: vi.fn(async () => ({ ...overview(), clients: answered })),
      answerClients: vi.fn(async (range: { since: string; until: string }, clients: number) => {
        answered = { ...range, leads: 2, clients };
        return answered;
      }),
    } as unknown as Partial<Api>);
    const store = await open(api);
    render(<Live store={store} />);
    await userEvent.type(screen.getByRole("textbox"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Записать" }));
    await settled();
    expect(api.answerClients).toHaveBeenCalledWith({ since: "2026-11-09", until: "2026-11-15" }, 1);
    expect(store.state.clients).toBe(1);
    expect(store.state.notice).toContain("Записал 1 из 2");
    expect(screen.getByText(/Записал 1 клиента/)).toBeInTheDocument();
  });

  it("отказ сервиса показывает как есть", async () => {
    const api = fakeApi({ answerClients: vi.fn(async () => { throw new ApiError(409, "Обращений было 2, поэтому клиентов не больше 2"); }) } as Partial<Api>);
    const store = await open(api);
    render(<Live store={store} />);
    await userEvent.type(screen.getByRole("textbox"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Записать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("не больше 2");
  });
});
