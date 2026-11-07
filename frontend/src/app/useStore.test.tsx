import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { initialState, shallowEqual, useStore} from "./store";
import { MockStore } from "../mock/MockStore";

describe("useStore", () => {
  it("сравнивает поверхностно", () => {
    expect(shallowEqual({ a: 1, b: [1] }, { a: 1, b: [1] })).toBe(false);
    const b = [1];
    expect(shallowEqual({ a: 1, b }, { a: 1, b })).toBe(true);
    expect(shallowEqual([1, 2], [1, 2])).toBe(true);
    expect(shallowEqual([1], { 0: 1 })).toBe(false);
    expect(shallowEqual(null, {})).toBe(false);
  });

  it("не перерисовывает экран, если выбранное не изменилось", () => {
    const store = new MockStore(initialState());
    let renders = 0;
    function Probe() {
      const { notice } = useStore(store, (s) => ({ notice: s.notice }));
      renders++;
      return <p>{notice ?? "-"}</p>;
    }
    const { container } = render(<Probe />);
    const first = renders;
    act(() => store.set({ input: "другое поле" }));
    expect(renders).toBe(first);
    act(() => store.set({ notice: "готово" }));
    expect(renders).toBe(first + 1);
    expect(container.textContent).toBe("готово");
  });
});
