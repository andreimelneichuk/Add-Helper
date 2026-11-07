import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { rub } from "../data";
import { mockFixtures } from "../../mock/fixtures";
const { STATS } = mockFixtures;
import { initialState} from "../store";
import { MockStore } from "../../mock/MockStore";
import { Stats } from "./Stats";

const num = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent;

describe("экран статистики", () => {
  it("смена периода меняет сводные числа и разбивку", async () => {
    render(<Stats store={new MockStore()} />);
    expect(screen.getByRole("button", { name: "7 дней" })).toHaveAttribute("aria-pressed", "true");
    expect(num("Обращения")).toBe(String(STATS.week.leads));
    expect(num("Цена обращения")).toBe(rub(STATS.week.cost / STATS.week.leads));
    expect(screen.getByRole("row", { name: /Первый визит со скидкой/ })).toHaveTextContent(rub(2300));

    await userEvent.click(screen.getByRole("button", { name: "С 1 ноября" }));

    expect(screen.getByRole("button", { name: "С 1 ноября" })).toHaveAttribute("aria-pressed", "true");
    expect(num("Обращения")).toBe(String(STATS.month.leads));
    expect(num("Цена обращения")).toBe(rub(STATS.month.cost / STATS.month.leads));
    expect(screen.getByRole("row", { name: /Первый визит со скидкой/ })).toHaveTextContent(rub(4000));
  });

  it("считает долю нажавших и цену клика", () => {
    render(<Stats store={new MockStore()} />);
    expect(num("CTR, доля нажавших")).toBe("3,7%");
    expect(num("Цена клика")).toBe(rub(5040 / 312));
  });

  it("в первые дни честно говорит, что данных мало, и не показывает пустую таблицу", () => {
    const { container } = render(<Stats store={new MockStore(initialState("work", "first"))} />);
    expect(screen.getByText(/Пока мало данных/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(num("Обращения")).toBe("1");
    expect(num("Цена обращения")).toBe(rub(640));
    expect(container.textContent).not.toMatch(/NaN|Infinity/);
  });

  it("называет объявление с новой ценой", () => {
    const store = new MockStore();
    store.run({ action: "price", value: 1400 });
    render(<Stats store={store} />);
    expect(screen.getByRole("row", { name: new RegExp(`Маникюр от ${rub(1400)}`) })).toBeInTheDocument();
    expect(screen.queryByText("Маникюр от 1 200 ₽")).toBeNull();
  });
});
