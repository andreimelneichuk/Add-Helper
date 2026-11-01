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

export type Overview = {
  today: string;
  since: string;
  money: Money[];
  alerts: Alert[];
  totals: Totals;
  ratios: Ratios;
  campaigns: Campaign[];
  days: Day[];
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

export type Draft = { headline: string; text: string; decision: "proposed" | "approved" | "declined" };
export type Rejection = { headline: string; text: string; reasons: string[] };
export type CreativeSet = {
  id: number;
  status: "queued" | "running" | "ready" | "failed";
  rules_version: string;
  source: "model" | "template" | null;
  drafts: Draft[];
  rejected: Rejection[];
  notes: string[];
  tokens_used: number;
  needs_review: boolean;
  created_at: string;
  finished_at: string | null;
};

export type Profile = {
  name?: string;
  services: { name: string; price: number | null }[];
  address: string;
  features: string[];
  niche: string;
  website: string;
};

export type Business = { id: string; name: string };
export type Me = { email: string | null; csrf_token: string };

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
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

export type Channels = {
  email: string | null;
  telegram: { available: boolean; linked: boolean };
};

export function createApi(businessId: string, csrf: string) {
  const base = `/api/businesses/${businessId}`;
  return {
    overview: () => request<Overview>(`${base}/overview`),
    history: () => request<HistoryLine[]>(`${base}/history`),
    profile: () => request<Profile>(`${base}/profile`),
    saveProfile: (profile: Profile) =>
      request<Profile>(`${base}/profile`, { method: "PUT", body: JSON.stringify(profile) }, csrf),
    sets: () => request<CreativeSet[]>(`${base}/creative-sets`),
    requestSet: () => request<{ id: number }>(`${base}/creative-sets`, { method: "POST" }, csrf),
    decide: (setId: number, index: number, decision: "approved" | "declined") =>
      request<{ decision: string }>(
        `${base}/creative-sets/${setId}/drafts/${index}`,
        { method: "POST", body: JSON.stringify({ decision }) },
        csrf,
      ),
    channels: () => request<Channels>("/api/me/notifications"),
    linkTelegram: () => request<{ url: string }>("/api/me/telegram", { method: "POST" }, csrf),
    unlinkTelegram: () => request<void>("/api/me/telegram", { method: "DELETE" }, csrf),
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
