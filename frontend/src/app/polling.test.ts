import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isAbort, pollUntil } from "./polling";

describe("pollUntil", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("возвращает значение, когда опрос закончен", async () => {
    let n = 0;
    const p = pollUntil({ task: async () => ++n, isDone: (v) => v >= 3, intervalMs: 100 });
    await vi.advanceTimersByTimeAsync(300);
    expect(await p).toEqual({ status: "done", value: 3 });
  });

  it("выходит по времени", async () => {
    const p = pollUntil({ task: async () => 0, isDone: () => false, intervalMs: 100, timeoutMs: 250 });
    await vi.advanceTimersByTimeAsync(500);
    expect(await p).toEqual({ status: "timeout" });
  });

  it("отмена сразу прекращает опрос и не оставляет таймеров", async () => {
    const controller = new AbortController();
    const task = vi.fn(async () => 0);
    const p = pollUntil({ task, isDone: () => false, intervalMs: 1000, signal: controller.signal });
    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    expect(await p).toEqual({ status: "aborted" });
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it("AbortError задачи считается отменой, остальные ошибки выбрасываются", async () => {
    const abort = Object.assign(new Error("x"), { name: "AbortError" });
    expect(await pollUntil({ task: async () => { throw abort; }, isDone: () => true })).toEqual({ status: "aborted" });
    await expect(pollUntil({ task: async () => { throw new Error("boom"); }, isDone: () => true })).rejects.toThrow("boom");
    expect(isAbort(abort)).toBe(true);
    expect(isAbort(new Error("x"))).toBe(false);
  });
});
