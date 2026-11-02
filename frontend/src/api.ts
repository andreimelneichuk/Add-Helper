// Клиент API сервиса. Деньги приходят целыми микроединицами, доли — базисными пунктами.

export type Totals = { impressions: number; clicks: number; cost: number; conversions: number };
export type Ratios = { ctr_bp: number | null; cpc: number | null; cpa: number | null; conversion_bp: number | null };

export type Period = {
  account: string;
  rules_version: string;
  starts_at: string;
  ends_at: string;
  hard_limit: number;
  reserve_bp: number;
  state: string;
  stop_pending: boolean;
  spent: number | null;
  data_as_of: string | null;
  opening_balance: number | null;
  opening_spent: number | null;
  last_balance: number | null;
  balance_checked_at: string | null;
};

export type Money = {
  period: Period;
  spendable: number;
  elapsed_bp: number;
  forecast: number | null;
  balance_gap: number | null;
  balance_tolerance: number;
};

export type Alert = { kind: string; details: Record<string, unknown>; created_at: string };

export type Campaign = {
  account: string;
  campaign_id: string;
  name: string;
  state: string;
  limit: number | null;
  totals: Totals;
  ratios: Ratios;
};

export type Day = { day: string; totals: Totals };

// Дни статистики по московскому времени, обе границы включены.
export type Range = { since: string; until: string };

export type Overview = {
  today: string;
  since: string;
  until: string;
  money: Money[];
  alerts: Alert[];
  totals: Totals;
  ratios: Ratios;
  previous: Totals;
  campaigns: Campaign[];
  days: Day[];
  /** Последний ответ владельца о клиентах. */
  clients: ClientAnswer | null;
};

export type ClientAnswer = { since: string; until: string; leads: number; clients: number };

export type AdLine = {
  account: string;
  ad_id: string;
  campaign_id: string;
  title: string;
  text: string;
  href: string;
  state: string;
  totals: Totals;
  ratios: Ratios;
};

export type CampaignDetail = {
  since: string;
  until: string;
  campaign: Omit<Campaign, "ratios">;
  ratios: Ratios;
  previous: Totals;
  days: Day[];
  ads: AdLine[];
};

export type AdDetail = {
  since: string;
  until: string;
  ad: Omit<AdLine, "ratios">;
  campaign_name: string | null;
  ratios: Ratios;
  previous: Totals;
  days: Day[];
  audience: Audience;
  basis: Basis;
};

export type HistoryLine = {
  ref: string;
  at: string;
  source: string;
  kind: string;
  target: string;
  status: string;
  explanation: string;
  before: Record<string, unknown>;
  after: Record<string, unknown> | null;
  rules_version: string | null;
};

// Откуда в объявлении факт: фрагмент текста и поле профиля.
export type FactSource = { fragment: string; label: string; key: string };
export type Basis = { sources: FactSource[]; unconfirmed: string[] };
// Кому показываем: настройки направления на площадке. Пустое поле — площадка не задала.
export type Audience = { placement: string; regions: string[]; phrases: string[]; schedule: string };

export type CreativeState = "ready" | "reserve" | "launch_pending" | "needs_owner" | "stopped" | "withdrawn";
export type Creative = {
  id: number;
  account: string;
  campaign_id: string;
  headline: string;
  text: string;
  source: string;
  state: CreativeState;
  reason: string;
  state_reason: string;
  sources: FactSource[];
  history: { at: string; state: CreativeState; reason: string }[];
  created_at: string;
  updated_at: string;
};
export type WritingSet = {
  id: number;
  account: string;
  campaign_id: string;
  reason: string;
  wanted: number;
  status: string;
  created_at: string;
  campaign_name: string | null;
};
export type Board = { creatives: (Creative & { campaign_name: string | null })[]; writing: WritingSet[] };
export type CreativeReport = { creative: Creative; campaign_name: string | null; audience: Audience; basis: Basis };

export type Profile = {
  name?: string;
  services: { name: string; price: number | null }[];
  address: string;
  features: string[];
  niche: string;
  website: string;
};

export type SiteFact = { field: "niche" | "address" | "service" | "feature"; value: string; price: number | null; quote: string; page: string };
export type SiteRead = {
  id: number;
  url: string;
  status: "queued" | "running" | "ready" | "failed";
  problem: string | null;
  problem_text: string | null;
  facts: SiteFact[];
  pages: string[];
};

export type Business = { id: string; name: string };
export type AdAccount = { id: string; platform: string; login: string; scopes: string[]; state: string };
export type Me = { email: string | null; csrf_token: string };

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(init.headers as Record<string, string> | undefined) };
  if (init.body) headers["Content-Type"] = "application/json";
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, { credentials: "same-origin", ...init, headers });
  if (!response.ok) {
    let detail = "Сервис не ответил";
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // Ответ без тела: оставляем общий текст.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Где и когда показывать и желаемая цена обращения; null — значение неизвестно или разное. */
export type Display = {
  campaigns: number;
  schedule: { days: number[]; start: number; end: number } | null;
  schedule_text: string | null;
  regions: number[] | null;
  regions_text: string | null;
  goal: number | null;
  catalog: { id: number; name: string }[];
  min_weekly_hours: number;
};

export type Channels = {
  email: string | null;
  telegram: { available: boolean; linked: boolean };
};

// Без периода сервис отдаёт последние 14 дней.
function period(range?: Range | null): string {
  return range ? `?since=${range.since}&until=${range.until}` : "";
}

/** Разбор сайта до входа: без сессии, черновик открывается ключом из ответа. */
export function createGuestApi() {
  return {
    readSite: (url: string) =>
      request<{ id: number; token: string; status: string; url: string }>("/api/site-reads", { method: "POST", body: JSON.stringify({ url }) }),
    siteRead: (id: number, token: string) =>
      request<SiteRead>(`/api/site-reads/${id}`, { headers: { "X-Read-Token": token } }),
    budgetOptions: () => request<{ min: number; max: number; presets: number[] }>("/api/budget-plan/options"),
  };
}

export const createBusiness = (name: string, csrf: string) =>
  request<Business>("/api/businesses", { method: "POST", body: JSON.stringify({ name }) }, csrf);

export function createApi(businessId: string, csrf: string) {
  const base = `/api/businesses/${businessId}`;
  const part = encodeURIComponent;
  return {
    overview: (range?: Range | null) => request<Overview>(`${base}/overview${period(range)}`),
    ads: (range?: Range | null) => request<AdLine[]>(`${base}/ads${period(range)}`),
    campaign: (account: string, id: string, range?: Range | null) =>
      request<CampaignDetail>(`${base}/campaigns/${part(account)}/${part(id)}${period(range)}`),
    ad: (account: string, id: string, range?: Range | null) =>
      request<AdDetail>(`${base}/ads/${part(account)}/${part(id)}${period(range)}`),
    history: () => request<HistoryLine[]>(`${base}/history`),
    profile: () => request<Profile>(`${base}/profile`),
    saveProfile: (profile: Profile) =>
      request<Profile>(`${base}/profile`, { method: "PUT", body: JSON.stringify(profile) }, csrf),
    creatives: () => request<Board>(`${base}/creatives`),
    creative: (id: number) => request<CreativeReport>(`${base}/creatives/${id}`),
    launch: (id: number) => request<CreativeReport>(`${base}/creatives/${id}/launch`, { method: "POST" }, csrf),
    withdraw: (id: number) => request<CreativeReport>(`${base}/creatives/${id}/withdraw`, { method: "POST" }, csrf),
    accounts: () => request<AdAccount[]>(`${base}/accounts`),
    connect: () => request<{ authorize_url: string }>(`${base}/accounts/connect`, { method: "POST" }, csrf),
    logout: () => request<void>("/api/auth/logout", { method: "POST" }, csrf),
    channels: () => request<Channels>("/api/me/notifications"),
    linkTelegram: () => request<{ url: string }>("/api/me/telegram", { method: "POST" }, csrf),
    unlinkTelegram: () => request<void>("/api/me/telegram", { method: "DELETE" }, csrf),
    setAdState: (account: string, adId: string, active: boolean, requestId: string) =>
      request<{ status: "applied" | "pending" }>(
        `${base}/ads/${part(account)}/${part(adId)}/state`,
        { method: "POST", body: JSON.stringify({ active, request_id: requestId }) },
        csrf,
      ),
    pause: (requestId: string) =>
      request<{ stopped: string[] }>(`${base}/pause`, { method: "POST", body: JSON.stringify({ request_id: requestId }) }, csrf),
    /** Снять паузу владельца; остановку защитой бюджета сервис не снимает. */
    resume: (requestId: string) =>
      request<{ resumed: string[]; refused: string[]; failed: string[] }>(`${base}/resume`, { method: "POST", body: JSON.stringify({ request_id: requestId }) }, csrf),
    /** Бюджет месяца в рублях; при снижении сервис сначала снижает лимиты в Яндексе. */
    setBudget: (rubles: number, requestId: string) =>
      request<{ status: string; cuts: number }>(`${base}/budget`, { method: "POST", body: JSON.stringify({ rubles, request_id: requestId }) }, csrf),
    display: () => request<Display>(`${base}/display`),
    /** Дни (0 — понедельник) и часы [start, end) показа; меняется через журнал. */
    setSchedule: (days: number[], start: number, end: number, requestId: string) =>
      request<{ applied: string[] }>(`${base}/display/schedule`, { method: "POST", body: JSON.stringify({ days, start, end, request_id: requestId }) }, csrf),
    setRegions: (regions: number[], requestId: string) =>
      request<{ applied: string[] }>(`${base}/display/regions`, { method: "POST", body: JSON.stringify({ regions, request_id: requestId }) }, csrf),
    readSite: (url: string) =>
      request<{ id: number; status: string; url: string }>(`${base}/site-reads`, { method: "POST", body: JSON.stringify({ url }) }, csrf),
    siteRead: () => request<SiteRead | null>(`${base}/site-reads/latest`),
    budgetPlan: () => request<{ rubles: number | null }>(`${base}/budget-plan`),
    saveBudgetPlan: (rubles: number) =>
      request<{ rubles: number }>(`${base}/budget-plan`, { method: "PUT", body: JSON.stringify({ rubles }) }, csrf),
    setGoal: (rubles: number) =>
      request<{ goal: number }>(`${base}/goal`, { method: "POST", body: JSON.stringify({ rubles }) }, csrf),
    answerClients: (range: Range, clients: number) =>
      request<ClientAnswer>(`${base}/clients`, { method: "POST", body: JSON.stringify({ ...range, clients }) }, csrf),
    rollback: (changeId: number) =>
      request<{ status: string }>(`${base}/changes/${changeId}/rollback`, { method: "POST" }, csrf),
    confirmBalance: (account: string) =>
      request<{ confirmed: boolean }>(
        `${base}/balance/confirm`,
        { method: "POST", body: JSON.stringify({ account }) },
        csrf,
      ),
  };
}

export type Api = ReturnType<typeof createApi>;

export async function openSession(): Promise<{ me: Me; businesses: Business[] } | null> {
  try {
    const me = await request<Me>("/api/me");
    const businesses = await request<Business[]>("/api/businesses");
    return { me, businesses };
  } catch {
    // Нет сервиса или входа: показываем прототип на тестовых данных.
    return null;
  }
}
