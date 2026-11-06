import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useObjectUrl } from "./useObjectUrl";

const create = vi.fn();
const revoke = vi.fn();
beforeEach(() => {
  let n = 0;
  create.mockImplementation(() => `blob:test/${++n}`);
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
});
afterEach(() => vi.clearAllMocks());

it("освобождает прежний адрес при смене файла и при размонтировании", () => {
  const a = new Blob(["a"]);
  const b = new Blob(["b"]);
  const { result, rerender, unmount } = renderHook(({ file }) => useObjectUrl(file), { initialProps: { file: a as Blob | null } });
  expect(result.current).toBe("blob:test/1");
  act(() => rerender({ file: b }));
  expect(revoke).toHaveBeenCalledWith("blob:test/1");
  expect(result.current).toBe("blob:test/2");
  act(() => rerender({ file: null }));
  expect(revoke).toHaveBeenCalledWith("blob:test/2");
  expect(result.current).toBeNull();
  unmount();
  expect(revoke).toHaveBeenCalledTimes(2);
});
