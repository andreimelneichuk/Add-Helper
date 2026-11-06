import { useCallback, useEffect, useRef } from "react";

/** Запрос отменили через AbortSignal: это не ошибка сервиса и пользователю не показывается. */
export function isAbort(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError";
}

export type PollOptions<T> = {
  /** Один опрос. Получает signal: запрос отменяется вместе с опросом. */
  task: (signal: AbortSignal) => Promise<T>;
  /** Опрос закончен: готово или отказ. Остальное опрашиваем дальше. */
  isDone: (value: T) => boolean;
  intervalMs?: number;
  timeoutMs?: number;
  signal?: AbortSignal;
};

export type PollResult<T> = { status: "done"; value: T } | { status: "timeout" } | { status: "aborted" };

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done);
  });
}

/** Опрашивает, пока не готово, не вышло время или не отменили. Ошибки задачи, кроме отмены, выбрасывает. */
export async function pollUntil<T>(options: PollOptions<T>): Promise<PollResult<T>> {
  const { task, isDone, intervalMs = 2000, timeoutMs = 45_000 } = options;
  const own = new AbortController();
  const outer = options.signal;
  const onOuterAbort = () => own.abort();
  if (outer?.aborted) own.abort();
  else outer?.addEventListener("abort", onOuterAbort);
  const started = Date.now();
  try {
    for (;;) {
      if (own.signal.aborted) return { status: "aborted" };
      let value: T;
      try {
        value = await task(own.signal);
      } catch (e) {
        if (isAbort(e) || own.signal.aborted) return { status: "aborted" };
        throw e;
      }
      if (own.signal.aborted) return { status: "aborted" };
      if (isDone(value)) return { status: "done", value };
      if (Date.now() - started >= timeoutMs) return { status: "timeout" };
      await sleep(intervalMs, own.signal);
    }
  } finally {
    outer?.removeEventListener("abort", onOuterAbort);
    own.abort();
  }
}

/**
 * Опрос, привязанный к экрану: новый запуск отменяет прежний, размонтирование и `cancel()` гасят таймер и запрос.
 */
export function usePollingTask() {
  const current = useRef<AbortController | null>(null);
  const cancel = useCallback(() => {
    current.current?.abort();
    current.current = null;
  }, []);
  useEffect(() => cancel, [cancel]);
  const run = useCallback(<T,>(options: Omit<PollOptions<T>, "signal">): Promise<PollResult<T>> => {
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    return pollUntil({ ...options, signal: controller.signal });
  }, []);
  return { run, cancel };
}
