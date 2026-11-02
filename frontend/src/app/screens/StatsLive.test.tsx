import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Api, CampaignDetail } from "../../api";
import { rub } from "../data";
import { fakeApi, line, MILLION, open } from "../testLive";
import { rangeProblem } from "./StatsLive";
import { Stats } from "./Stats";

const num = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling?.textContent;
const ratios = { ctr_bp: null, cpc: null, cpa: null, conversion_bp: null };

function detail(): CampaignDetail {
  const totals = { impressions: 900, clicks: 40, cost: 2000 * MILLION, conversions: 4 };
  return {
    since: "2026-11-09", until: "2026-11-15", ratios, previous: { impressions: 0, clicks: 0, cost: 0, conversions: 2 }, days: [],
    campaign: { account: "anna", campaign_id: "1", name: "Маникюр", state: "on", limit: 500 * MILLION, totals },
    ads: [{ ...line(), totals }],
  };
}

describe("статистика на настоящих данных", () => {
  it("свой период запрашивает у сервиса и пересчитывает сводку", async () => {
    const api = fakeApi();
    render(<Stats store={await open(api)} />);
    await userEvent.click(screen.getByRole("button", { name: "Свой период" }));
    const form = screen.getByRole("form", { name: "Свой период" });
    const [since, until] = within(form).getAllByDisplayValue(/2026/) as HTMLInputElement[];
    expect(until!.value).toBe("2026-11-15");
    await userEvent.clear(since!);
    await userEvent.type(since!, "2026-11-02");
    vi.mocked(api.overview).mockClear();
    await userEvent.click(within(form).getByRole("button", { name: "Показать" }));

    expect(api.overview).toHaveBeenCalledWith({ since: "2026-11-02", until: "2026-11-15" });
    expect(api.ads).toHaveBeenLastCalledWith({ since: "2026-11-02", until: "2026-11-15" });
    // В подставном обзоре 700 ₽ и 2 обращения 15-го и 100 ₽ и 1 обращение 2-го.
    expect(num("Обращения")).toBe("3");
    expect(num("Цена обращения")).toBe(rub(800 / 3));
    expect(screen.getByRole("button", { name: /^2 нояб.* – 15 нояб/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("не пускает будущие дни и перевёрнутый период", () => {
    expect(rangeProblem({ since: "2026-11-10", until: "2026-11-16" }, "2026-11-15")).toContain("Будущих");
    expect(rangeProblem({ since: "2026-11-10", until: "2026-11-01" }, "2026-11-15")).toContain("позже");
    expect(rangeProblem({ since: "2025-01-01", until: "2026-11-15" }, "2026-11-15")).toContain("года");
    expect(rangeProblem({ since: "2026-11-01", until: "2026-11-15" }, "2026-11-15")).toBeNull();
  });

  it("направление открывает свой отчёт за выбранный период", async () => {
    const api = fakeApi({ campaign: vi.fn(async () => detail()) } as Partial<Api>);
    render(<Stats store={await open(api)} />);
    await userEvent.click(screen.getByRole("button", { name: "Отчёт по направлению «Маникюр»" }));

    expect(api.campaign).toHaveBeenCalledWith("anna", "1", { since: "2026-11-09", until: "2026-11-15" });
    expect(await screen.findByRole("heading", { name: "Маникюр" })).toBeInTheDocument();
    expect(num("Цена обращения")).toBe(rub(500));
    expect(screen.getByText(/лимит в день 500/)).toHaveTextContent("В прошлом таком же периоде 2 обращения");
    expect(screen.getByRole("button", { name: "Открыть «Маникюр»" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Назад к статистике" }));
    expect(screen.getByRole("button", { name: "7 дней" })).toHaveAttribute("aria-pressed", "true");
  });
});
