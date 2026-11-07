import type { Api } from "../api";
import { History } from "./screens/History";
import { fakeApi, open } from "./testLive";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

describe("История и отмена: регрессии (BUG-…)", () => {
  describe("BUG-032: History undo race condition — button disappears and state shows 'Отменяю…'", () => {
    it("32.1: does not show 'Отменить' button while rollback API call is in flight", async () => {
      let resolveRollback: (value: { status: "applied" }) => void;
      const rollbackPromise = new Promise<{ status: "applied" }>((resolve) => {
        resolveRollback = resolve;
      });

      const api = fakeApi({
        rollbackPreview: vi.fn(async () => ({
          possible: true,
          status: "ready",
          directions: 1,
          text: "Вернётся прежний лимит.",
        })),
        rollback: vi.fn(() => rollbackPromise),
      } as unknown as Partial<Api>);

      const store = await open(api);
      render(<History store={store} />);

      // Initially, "Отменить" button is visible
      const undoBtn = await screen.findByRole("button", { name: "Отменить" });
      await userEvent.click(undoBtn);

      // Confirm button appears
      const confirmBtn = await screen.findByRole("button", { name: "Да, отменить" });
      await userEvent.click(confirmBtn);

      // While rollback is in flight:
      // "Отменить" must NOT reappear, and status should show "Отменяю…"
      expect(screen.queryByRole("button", { name: "Отменить" })).not.toBeInTheDocument();
      expect(screen.getByText("Отменяю…")).toBeInTheDocument();

      // Resolve the rollback
      resolveRollback!({ status: "applied" });
      await waitFor(() => {
        expect(screen.getByText("Отменено вами")).toBeInTheDocument();
      });
    });
  });
});
