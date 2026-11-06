import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Поиск по тексту приводит неразрывный пробел к обычному, а поиск по имени нет: в тестах суммы пишем с обычным.
vi.mock("./app/data", async (original) => {
  const data = await original<typeof import("./app/data")>();
  return { ...data, rub: (n: number) => data.rub(n).replace(/ /g, " ") };
});

// В новых версиях Node свой пустой localStorage заслоняет хранилище из jsdom.
const items = new Map<string, string>();
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
    clear: () => items.clear(),
  },
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// Тесты гоняют прототип на его подставных данных; в боевой сборке их нет.
import { installFixtures } from "./app/fixtures";
import { mockFixtures } from "./mock/fixtures";
installFixtures(mockFixtures);
