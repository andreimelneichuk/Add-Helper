import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdDetail, CampaignDetail, Overview } from "../api";
import { percent, rub, rubExact } from "../format";
import { Cabinet } from "./Cabinet";

const M = 1_000_000;
const totals = (impressions: number, clicks: number, cost: number, conversions: number) => ({ impressions, clicks, cost: cost * M, conversions });

function overview(gap: number, alerts: Overview["alerts"] = []): Overview {
  return {
    today: "2026-11-10",
    since: "2026-10-28",
    until: "2026-11-10",
    previous: totals(1800, 60, 900, 2),
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
    // Период приходит в строке запроса: маршрут ищем по пути, а адрес проверяем в calls.
    const key = `${init?.method ?? "GET"} ${url.replace("/api/businesses/b1", "").split("?")[0]}`;
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

  it("объявления: система работает сама, владелец решает только спорное", async () => {
    const waiting = {
  id: 5, account: "lak-studio", campaign_id: "102", headline: "Педикюр в студии «Лак»", text: "Педикюр за 2200 ₽.",
  source: "model", state: "needs_owner", reason: "refill", state_reason: "place_free", sources: [],
  history: [
    { at: "2026-11-09T07:00:00Z", state: "ready", reason: "written" },
    { at: "2026-11-09T07:05:00Z", state: "needs_owner", reason: "place_free" },
  ],
  created_at: "2026-11-09T07:00:00Z", updated_at: "2026-11-09T07:05:00Z", campaign_name: "Педикюр — поиск",
};
    const board = {
      creatives: [
        waiting,
        { ...waiting, id: 6, headline: "Маникюр у Профсоюзной", state: "launch_pending", campaign_name: "Маникюр — поиск" },
        { ...waiting, id: 7, headline: "Педикюр за 2000 ₽", state: "stopped", state_reason: "fact_changed" },
      ],
      writing: [
        { id: 3, account: "lak-studio", campaign_id: "103", reason: "refill", wanted: 2, status: "running", created_at: "2026-11-10T08:58:00Z", campaign_name: "Маникюр — сайты" },
      ],
    };
    const live = {
      account: "lak-studio", ad_id: "1011", campaign_id: "101", title: "Маникюр с покрытием 1500 ₽", text: "Стерильные инструменты.",
      href: "https://lak.example/", state: "on", totals: totals(2000, 80, 1000, 4), ratios: overview(140).ratios,
    };
    const calls = serve({
      "GET /overview": overview(140),
      "GET /creatives": board,
      "GET /ads": [live, { ...live, ad_id: "1013", title: "Отключённое", state: "suspended" }],
      "POST /creatives/5/launch": { creative: { ...waiting, state: "launch_pending" }, campaign_name: null, audience: { placement: "", regions: [], phrases: [], schedule: "" }, basis: { sources: [], unconfirmed: [] } },
    });
    window.location.hash = "ads";
    render(<Cabinet me={ME} business={BUSINESS} />);

    expect(await screen.findByText(/Сейчас 1 объявление работает, 1 готовим к запуску, пишем новые для 1 направления. 1 ждёт вашего решения./)).toBeInTheDocument();
    const decide = screen.getByRole("region", { name: "Нужно ваше решение" });
    expect(within(decide).getByText("Педикюр в студии «Лак»")).toBeInTheDocument();
    expect(screen.queryByText("Отключённое")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Маникюр с покрытием 1500 ₽/ })).toHaveAttribute("href", expect.stringMatching(/^#ad\/lak-studio\/1011/));
    expect(screen.getByText(/Пишем 2 объявления на освободившееся место в «Маникюр — сайты»/)).toBeInTheDocument();
    expect(screen.getByText("Сняты: 1")).toBeInTheDocument();
    // Внутренняя кухня владельцу не нужна: ни «модели», ни «судьи», ни версий правил.
    expect(document.body.textContent).not.toMatch(/модел|судь|creatives-/i);

    await userEvent.click(within(decide).getByRole("button", { name: "Запустить" }));
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe("/api/businesses/b1/creatives/5/launch");
    expect((post?.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
  });

  it("отчёт объявления: что происходит, кому показываем и откуда факты", async () => {
    const waiting = {
  id: 5, account: "lak-studio", campaign_id: "102", headline: "Педикюр в студии «Лак»", text: "Педикюр за 2200 ₽.",
  source: "model", state: "needs_owner", reason: "refill", state_reason: "place_free", sources: [],
  history: [
    { at: "2026-11-09T07:00:00Z", state: "ready", reason: "written" },
    { at: "2026-11-09T07:05:00Z", state: "needs_owner", reason: "place_free" },
  ],
  created_at: "2026-11-09T07:00:00Z", updated_at: "2026-11-09T07:05:00Z", campaign_name: "Педикюр — поиск",
};
    serve({
      "GET /overview": overview(140),
      "GET /creatives/5": {
        creative: waiting, campaign_name: "Педикюр — поиск",
        audience: { placement: "search", regions: ["Москва"], phrases: ["педикюр профсоюзная"], schedule: "каждый день, 9:00–21:00" },
        basis: { sources: [{ fragment: "2200 ₽", label: "цена услуги «Педикюр»", key: "price:Педикюр:2200" }], unconfirmed: [] },
      },
      "POST /creatives/5/withdraw": {
        creative: { ...waiting, state: "withdrawn", state_reason: "owner" }, campaign_name: "Педикюр — поиск",
        audience: { placement: "", regions: [], phrases: [], schedule: "" }, basis: { sources: [], unconfirmed: [] },
      },
    });
    window.location.hash = "creative/5";
    render(<Cabinet me={ME} business={BUSINESS} />);

    expect(await screen.findByRole("heading", { level: 1, name: "Педикюр в студии «Лак»" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Объявления" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/Без вас не запускаем/)).toBeInTheDocument();
    expect(screen.getByText("Тем, кто в регионе: Москва")).toBeInTheDocument();
    expect(screen.getByText("✓ Все факты — из вашего профиля")).toBeInTheDocument();
    expect(screen.getByText("цена услуги «Педикюр»")).toBeInTheDocument();
    expect(screen.getByText(/Писали на освободившееся место/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Не запускать" }));
    expect(await screen.findByText("Вы решили его не запускать.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Запустить" })).not.toBeInTheDocument();
  });

  it("период: быстрый выбор и свои даты меняют запрос и адрес", async () => {
    const calls = serve({ "GET /overview": overview(140) });
    window.location.hash = "stats";
    render(<Cabinet me={ME} business={BUSINESS} />);

    await userEvent.click(await screen.findByRole("button", { name: "7 дней" }));
    await waitFor(() => expect(calls.at(-1)?.url).toBe("/api/businesses/b1/overview?since=2026-11-04&until=2026-11-10"));
    expect(window.location.hash).toBe("#stats?since=2026-11-04&until=2026-11-10");
    expect(screen.getByRole("button", { name: "7 дней" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: /7 дней: / }));
    const dialog = screen.getByRole("dialog", { name: "Период отчёта" });
    const [since, until] = within(dialog).getAllByDisplayValue(/2026-/) as HTMLInputElement[];
    fireEvent.change(since!, { target: { value: "2026-11-10" } });
    fireEvent.change(until!, { target: { value: "2026-11-01" } });
    expect(within(dialog).getByText("Начало позже конца")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Показать" })).toBeDisabled();
    fireEvent.change(since!, { target: { value: "2026-10-01" } });
    fireEvent.change(until!, { target: { value: "2026-10-31" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Показать" }));
    await waitFor(() => expect(calls.at(-1)?.url).toBe("/api/businesses/b1/overview?since=2026-10-01&until=2026-10-31"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("график: стрелки показывают все показатели дня", async () => {
    serve({ "GET /overview": overview(140) });
    window.location.hash = "stats";
    render(<Cabinet me={ME} business={BUSINESS} />);

    const chart = await screen.findByLabelText(/График по дням/);
    chart.focus();
    await userEvent.keyboard("{End}");
    const tip = within(chart).getByRole("status");
    expect(tip).toHaveTextContent("10 ноя, вт");
    expect(tip).toHaveTextContent("Расход600 ₽");
    expect(tip).toHaveTextContent("Цена обращения300 ₽");
    await userEvent.keyboard("{ArrowLeft}");
    expect(within(chart).getByRole("status")).toHaveTextContent("400 ₽");
    await userEvent.keyboard("{Escape}");
    expect(within(chart).queryByRole("status")).not.toBeInTheDocument();
  });

  it("таблица сортируется щелчком по заголовку", async () => {
    serve({ "GET /overview": overview(140) });
    window.location.hash = "campaigns";
    render(<Cabinet me={ME} business={BUSINESS} />);

    const table = await screen.findByRole("table");
    const names = () => within(table).getAllByRole("link").map((a) => a.textContent);
    await userEvent.click(within(table).getByRole("button", { name: /Расход/ }));
    expect(names()).toEqual(["Маникюр — поиск", "Наращивание — поиск"]);
    expect(within(table).getByRole("columnheader", { name: /Расход/ })).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(within(table).getByRole("button", { name: /Расход/ }));
    expect(names()).toEqual(["Наращивание — поиск", "Маникюр — поиск"]);
  });

  it("отчёт направления и объявления открываются из таблицы с тем же периодом", async () => {
    const view = overview(140);
    const ad = {
      account: "lak-studio", ad_id: "1011", campaign_id: "101", title: "Маникюр с покрытием 1500 ₽",
      text: "Стерильные инструменты.", href: "https://lak.example/", state: "on", totals: totals(2000, 80, 1000, 4),
    };
    const campaign: CampaignDetail = {
      since: "2026-11-04", until: "2026-11-10", campaign: view.campaigns[0]!, ratios: view.ratios, previous: view.previous,
      days: view.days, ads: [{ ...ad, ratios: view.ratios }],
    };
    const detail: AdDetail = {
      since: "2026-11-04", until: "2026-11-10", ad, campaign_name: "Маникюр — поиск", ratios: view.ratios, previous: view.previous, days: view.days,
      audience: { placement: "search", regions: ["Москва"], phrases: ["маникюр профсоюзная"], schedule: "" },
      basis: { sources: [{ fragment: "Стерильные инструменты", label: "особенность из профиля", key: "feature:x" }], unconfirmed: ["1500"] },
    };
    const calls = serve({ "GET /overview": view, "GET /campaigns/lak-studio/101": campaign, "GET /ads/lak-studio/1011": detail });
    window.location.hash = "campaigns?since=2026-11-04&until=2026-11-10";
    render(<Cabinet me={ME} business={BUSINESS} />);

    await userEvent.click(await screen.findByText("Маникюр — поиск"));
    expect(await screen.findByRole("heading", { level: 1, name: "Маникюр — поиск" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#campaigns/lak-studio/101?since=2026-11-04&until=2026-11-10");
    expect(calls.at(-1)?.url).toBe("/api/businesses/b1/campaigns/lak-studio/101?since=2026-11-04&until=2026-11-10");
    expect(screen.getByRole("heading", { name: "Объявления направления" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("link", { name: "Маникюр с покрытием 1500 ₽" }));
    expect(await screen.findByRole("heading", { name: "Как видят в поиске" })).toBeInTheDocument();
    expect(calls.at(-1)?.url).toBe("/api/businesses/b1/ads/lak-studio/1011?since=2026-11-04&until=2026-11-10");
    expect(screen.getByText("lak.example")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Маникюр — поиск" })).toHaveAttribute("href", "#campaigns/lak-studio/101?since=2026-11-04&until=2026-11-10");
    expect(screen.getByText("В поиске Яндекса")).toBeInTheDocument();
    expect(screen.getByText("маникюр профсоюзная")).toBeInTheDocument();
    expect(screen.getByText(/Числа 1500 нет в вашем профиле/)).toHaveTextContent("обновите профиль");
  });
});
