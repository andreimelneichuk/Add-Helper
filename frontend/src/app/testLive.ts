// Поддельный API для тестов нового интерфейса на настоящих данных.
import { vi } from "vitest";
import type { AdLine, Api, Display, HistoryLine, Overview } from "../api";
import { LiveStore, loadLive } from "./live";

export const MILLION = 1_000_000;
const totals = (cost: number, conversions: number) => ({ impressions: 100, clicks: 10, cost: cost * MILLION, conversions });
const ratios = { ctr_bp: null, cpc: null, cpa: null, conversion_bp: null };
export const line = (over: Partial<AdLine> = {}): AdLine => ({ account: "anna", ad_id: "11", campaign_id: "1", title: "Маникюр", text: "Текст", href: "", state: "on", totals: totals(700, 2), ratios, ...over });
export const change: HistoryLine = { ref: "change-5", at: "2026-11-01T10:00:00Z", source: "user", kind: "pause_ad", target: "ad:11", status: "applied", explanation: "Отключил: вы попросили.", before: { active: true }, after: { active: false }, rules_version: null };

export function overview(alerts: Overview["alerts"] = []): Overview {
  const period = { account: "anna", rules_version: "1", starts_at: "2026-11-01T00:00:00Z", ends_at: "2026-12-01T00:00:00Z", hard_limit: 20000 * MILLION, reserve_bp: 0, state: "active", stop_pending: false, spent: 1500 * MILLION, data_as_of: null, opening_balance: null, opening_spent: null, last_balance: 7000 * MILLION, balance_checked_at: null };
  return {
    today: "2026-11-15", since: "2026-11-01", until: "2026-11-15", alerts, ratios, clients: null, totals: totals(0, 0), previous: totals(0, 0),
    money: [{ period, spendable: 0, elapsed_bp: 0, forecast: null, balance_gap: 0, balance_tolerance: 0 }],
    campaigns: [{ account: "anna", campaign_id: "1", name: "Маникюр", state: "on", limit: null, totals: totals(0, 0), ratios }],
    days: [{ day: "2026-11-15", totals: totals(700, 2) }, { day: "2026-11-02", totals: totals(100, 1) }],
  };
}

export const display = (): Display => ({
  campaigns: 1,
  schedule: { days: [0, 1, 2, 3, 4, 5, 6], start: 0, end: 24 },
  schedule_text: "круглосуточно",
  regions: [213],
  regions_text: "Москва",
  goal: null,
  catalog: [{ id: 1, name: "Москва и область" }, { id: 213, name: "Москва" }, { id: 2, name: "Санкт-Петербург" }],
  min_weekly_hours: 8,
});

export function fakeApi(over: Partial<Api> = {}) {
  return {
    overview: vi.fn(async () => overview()),
    ads: vi.fn(async () => [line()]),
    history: vi.fn(async () => [change]),
    profile: vi.fn(async () => ({ services: [{ name: "Маникюр", price: 1200 }], address: "Москва", features: [], niche: "", website: "" })),
    ad: vi.fn(async () => ({ basis: { sources: [{ fragment: "1 200", label: "Цена", key: "price" }], unconfirmed: [] } })),
    setAdState: vi.fn(async () => ({ status: "applied" as const })),
    pause: vi.fn(async () => ({ stopped: ["1"] })),
    rollback: vi.fn(async () => ({ status: "applied" })),
    confirmBalance: vi.fn(async () => ({ confirmed: true })),
    saveProfile: vi.fn(async (p: unknown) => p),
    accounts: vi.fn(async () => [{ id: "a1", platform: "yandex_direct", login: "anna", scopes: [], state: "connected" }]),
    connect: vi.fn(async () => ({ authorize_url: "https://oauth.yandex.ru/authorize?x=1" })),
    logout: vi.fn(async () => undefined),
    channels: vi.fn(async () => ({ email: "anna@example.ru", telegram: { available: true, linked: false } })),
    linkTelegram: vi.fn(async () => ({ url: "https://t.me/bot?start=x" })),
    unlinkTelegram: vi.fn(async () => undefined),
    creatives: vi.fn(async () => ({ creatives: [], writing: [] })),
    launch: vi.fn(async () => ({})),
    withdraw: vi.fn(async () => ({})),
    campaign: vi.fn(async () => { throw new Error("не задано в тесте"); }),
    display: vi.fn(async () => display()),
    setSchedule: vi.fn(async () => ({ applied: ["anna:1"] })),
    setRegions: vi.fn(async () => ({ applied: ["anna:1"] })),
    setGoal: vi.fn(async (rubles: number) => ({ goal: rubles })),
    ...over,
  } as unknown as Api;
}

export const open = async (api: Api) => new LiveStore(api, await loadLive(api, "Салон Анны"));
export const settled = () => new Promise((resolve) => setTimeout(resolve));

