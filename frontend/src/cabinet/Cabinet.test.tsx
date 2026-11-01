import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Overview } from "../api";
import { percent, rub, rubExact } from "../format";
import { Cabinet } from "./Cabinet";

const M = 1_000_000;
const totals = (impressions: number, clicks: number, cost: number, conversions: number) => ({ impressions, clicks, cost: cost * M, conversions });

function overview(gap: number, alerts: Overview["alerts"] = []): Overview {
  return {
    today: "2026-11-10",
    since: "2026-10-28",
    money: [
      {
        period: {
          account: "lak-studio", rules_version: "budget-1",
          starts_at: "2026-10-31T21:00:00Z", ends_at: "2026-11-30T21:00:00Z",
          hard_limit: 40_000 * M, reserve_bp: 1000, state: "active", stop_pending: false,
          spent: 9_000 * M, data_as_of: "2026-11-10T08:00:00Z",
          opening_balance: 60_000 * M, opening_spent: 0, last_balance: (51_000 - gap) * M,
          balance_checked_at: "2026-11-10T08:56:00Z",
        },
        spendable: 36_000 * M, elapsed_bp: 3_125, forecast: 28_800 * M,
        balance_gap: gap * M, balance_tolerance: 800 * M,
      },
    ],
    alerts,
    totals: totals(2000, 80, 1000, 4),
    ratios: { ctr_bp: 400, cpc: 12_500_000, cpa: 250 * M, conversion_bp: 500 },
    campaigns: [
      {
        account: "lak-studio", campaign_id: "101", name: "Маникюр — поиск", state: "on", limit: 600 * M,
        totals: totals(2000, 80, 1000, 4), ratios: { ctr_bp: 400, cpc: 12_500_000, cpa: 250 * M, conversion_bp: 500 },
      },
      {
        account: "lak-studio", campaign_id: "104", name: "Наращивание — поиск", state: "suspended", limit: 300 * M,
        totals: totals(0, 0, 0, 0), ratios: { ctr_bp: null, cpc: null, cpa: null, conversion_bp: null },
      },
    ],
    days: [
      { day: "2026-11-09", totals: totals(1000, 40, 400, 2) },
      { day: "2026-11-10", totals: totals(1000, 40, 600, 2) },
    ],
  };
}

function serve(routes: Record<string, unknown>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = `${init?.method ?? "GET"} ${url.replace("/api/businesses/b1", "")}`;
    const body = typeof routes[key] === "function" ? (routes[key] as () => unknown)() : routes[key];
    return new Response(JSON.stringify(body ?? []), { status: body === undefined ? 404 : 200 });
  }));
  return calls;
}

const ME = { email: "owner@example.test", csrf_token: "csrf-1" };
const BUSINESS = { id: "b1", name: "Студия маникюра «Лак»" };

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("форматирование", () => {
  it("округляет только при показе", () => {
    expect(rub(1_499_999)).toBe("1 ₽");
    expect(rub(12_345 * M)).toBe("12 345 ₽");
    expect(rubExact(12_500_000)).toBe("12,50 ₽");
    expect(percent(400)).toBe("4,00 %");
    expect(percent(null)).toBe("—");
  });
});

describe("кабинет", () => {
  it("сводка: всё в порядке, деньги и сверка сходятся", async () => {
    serve({ "GET /overview": overview(140), "GET /history": [] });
    render(<Cabinet me={ME} business={BUSINESS} />);

    expect(await screen.findByText(/Реклама работает/)).toBeInTheDocument();
    expect(screen.getByText("Студия маникюра «Лак»")).toBeInTheDocument();
    const money = screen.getByRole("region", { name: /Деньги/ });
    expect(within(money).getByText(/✓ Сходится/)).toBeInTheDocument();
    expect(within(money).getByText("140 ₽")).toBeInTheDocument();
    expect(within(money).getByText(/допуск 800 ₽/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Расход по дням" })).toBeInTheDocument();
  });

  it("расхождение со счётом объясняет и подтверждается с CSRF", async () => {
    let confirmed = false;
    const calls = serve({
      "GET /overview": () => (confirmed ? overview(0) : overview(1240, [{ kind: "balance_mismatch", details: {}, created_at: "2026-11-10T08:56:00Z" }])),
      "GET /history": [],
      "POST /balance/confirm": () => { confirmed = true; return { confirmed: true }; },
    });
    render(<Cabinet me={ME} business={BUSINESS} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Счёт не сходится со статистикой");
    expect(alert).toHaveTextContent("1 240 ₽");
    expect(alert).toHaveTextContent("остановили показы");
    await userEvent.click(within(alert).getByRole("button", { name: "Я знаю причину" }));
    expect(alert).toHaveTextContent("Показы сами не включатся");
    await userEvent.click(within(alert).getByRole("button", { name: "Да, всё верно" }));

    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe("/api/businesses/b1/balance/confirm");
    expect((post?.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ account: "lak-studio" });
    expect(await screen.findByText(/Реклама работает/)).toBeInTheDocument();
  });

  it("реклама: таблица направлений с итогами и фильтром", async () => {
    serve({ "GET /overview": overview(140), "GET /history": [] });
    window.location.hash = "campaigns";
    render(<Cabinet me={ME} business={BUSINESS} />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Маникюр — поиск")).toBeInTheDocument();
    expect(within(table).getByText("нет обращений")).toBeInTheDocument();
    expect(within(table).getByText("Итого").closest("tr")).toHaveTextContent("600 ₽");
    await userEvent.click(screen.getByRole("tab", { name: /Остановлены/ }));
    expect(screen.queryByText("Маникюр — поиск")).not.toBeInTheDocument();
    expect(screen.getByText("Наращивание — поиск")).toBeInTheDocument();
  });

  it("уведомления: почта, подключение Telegram с CSRF и отключение", async () => {
    let linked = false;
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const calls = serve({
      "GET /overview": overview(140),
      "GET /api/me/notifications": () => ({ email: "owner@example.test", telegram: { available: true, linked } }),
      "POST /api/me/telegram": () => { linked = true; return { url: "https://t.me/adhelper_bot?start=abc" }; },
    });
    window.location.hash = "notify";
    render(<Cabinet me={ME} business={BUSINESS} />);

    expect(await screen.findByText("owner@example.test")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Подключить" }));
    const post = calls.find((c) => c.init?.method === "POST");
    expect((post?.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect(open).toHaveBeenCalledWith("https://t.me/adhelper_bot?start=abc", "_blank", "noopener");
    expect(screen.getByText(/нажмите «Запустить»/)).toBeInTheDocument();
    expect(screen.getByText("Счёт не сходится со статистикой")).toBeInTheDocument();
  });

  it("объявления: превью, решение владельца и причины снятия", async () => {
    const calls = serve({
      "GET /overview": overview(140),
      "GET /profile": { services: [], address: "", features: [], niche: "", website: "https://lak.example" },
      "GET /creative-sets": [
        {
          id: 7, status: "ready", rules_version: "creatives-2", source: "model", notes: [], tokens_used: 10,
          needs_review: false, created_at: "2026-11-09T07:00:00Z", finished_at: "2026-11-09T07:01:00Z",
          drafts: [{ headline: "Маникюр с покрытием 1500 ₽", text: "Стерильные инструменты.", decision: "proposed" }],
          rejected: [{ headline: "Лучший маникюр", text: "Скидка 30%!", reasons: ["unconfirmed_number"] }],
        },
      ],
      "POST /creative-sets/7/drafts/0": { decision: "approved" },
    });
    window.location.hash = "ads";
    render(<Cabinet me={ME} business={BUSINESS} />);

    expect(await screen.findByText("Маникюр с покрытием 1500 ₽")).toBeInTheDocument();
    expect(await screen.findByText("lak.example")).toBeInTheDocument();
    expect(screen.getByText("Число не из вашего профиля")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Одобрить" }));
    const post = calls.find((c) => c.url.endsWith("/drafts/0"));
    expect(JSON.parse(String(post?.init?.body))).toEqual({ decision: "approved" });
  });
});
