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
  /** Картинка, с которой объявление ушло в Яндекс; null — без картинки. */
  image_id?: number | null;
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
  /** Уже отменено новой записью журнала. */
  reverted?: boolean;
};

// Откуда в объявлении факт: фрагмент текста и поле профиля.
export type FactSource = { fragment: string; label: string; key: string };
export type Basis = { sources: FactSource[]; unconfirmed: string[] };
// Кому показываем: настройки направления на площадке. Пустое поле — площадка не задала.
export type Audience = { placement: string; regions: string[]; phrases: string[]; schedule: string };

export type CreativeState = "ready" | "reserve" | "launch_pending" | "needs_owner" | "stopped" | "withdrawn" | "moderation" | "running" | "rejected";
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

/** Работающее объявление с числом, которого в профиле больше нет. */
export type OutdatedAd = { account: string; ad_id: string; headline: string; text: string; numbers: string[]; replacing: boolean };
export type SavedProfile = Profile & { outdated?: OutdatedAd[] };

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

/** Объявление писателя в мастере: facts — на какие поля черновика оно опирается. */
export type WizardAd = { headline: string; text: string; facts: string[] };
export type AdSet = {
  id: number;
  status: "queued" | "running" | "ready" | "failed";
  source: "model" | "template" | null;
  /** Писатель не ответил или его тексты сняты проверкой: собрано кодом из фактов. */
  template: boolean;
  /** Строгая сфера: перед запуском каждое объявление покажем владельцу. */
  needs_review: boolean;
  problem_text: string | null;
  ads: WizardAd[];
};
/** Черновик профиля для писателя: только подтверждённые владельцем факты. */
export type AdDraftProfile = Omit<Profile, "website"> & { force?: boolean };
/** Выбор владельца в мастере: какой набор и какие объявления из него оставить. */
export type AdsChoice = { id: number; token: string; kept: number[] };

export type Business = { id: string; name: string };
export type AdAccount = { id: string; platform: string; login: string; scopes: string[]; state: string };
export type AccountBalance = { balance_micros: number | null; problem: string | null };
export type Me = { email: string | null; csrf_token: string };

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export function formatErrorDetail(raw: unknown): string {
  if (typeof raw === "string" && raw.trim()) return raw;
  if (Array.isArray(raw) && raw.length > 0) {
    const msgs = raw
      .map((item) => {
        if (typeof item === "object" && item !== null && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return typeof item === "string" ? item : "";
      })
      .filter(Boolean);
    if (msgs.length > 0) return msgs.join("; ");
  }
  if (typeof raw === "object" && raw !== null && "msg" in raw) {
    return String((raw as { msg: unknown }).msg);
  }
  return "Сервис не ответил";
}

async function request<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(init.headers as Record<string, string> | undefined) };
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, { credentials: "same-origin", ...init, headers });
  if (!response.ok) {
    let detail = "Сервис не ответил";
    try {
      const data = (await response.json()) as { detail?: unknown; message?: unknown };
      detail = formatErrorDetail(data.detail ?? data.message ?? detail);
    } catch {
      // Ответ без тела: оставляем общий текст.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Режим бюджета владельца: раздельный на сайт или общий пул на все сайты. */
export type BudgetPortfolio = { mode: "separate" | "shared"; rubles: number | null; businesses: number };
export type PortfolioSaved = { mode: "separate" | "shared"; rubles: number | null; shares: { business_id: string; rubles: number }[] };

/** Своё фото владельца: уже приведено к требованиям Яндекса, без метаданных. */
export type Photo = { id: number; width: number; height: number; url: string };
export type Photos = { photos: Photo[]; max: number };
export type SiteImage = { url: string; preview: string };

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

/** Как сервис понял сообщение чата. Ничего не меняет: выполняется после «да» владельца. */
/** Карточка «Сегодня»: набор и порядок решают правила сервиса, числа подставляет код. */
export type TodayCard = { kind: string; tone: "bad" | "warn" | "info" | "good"; title: string; text: string; action: "balance" | "ads" | "settings" | "resume" | "history" | "undo" | "test_offer" | "test_cancel" | null; ref: string | null; rules: string };

/** Что будет после отмены: текст собирает сервис из журнала, до нажатия. */
export type UndoPreview = { possible: boolean; status: string; text: string; directions: number };

export type ChatCommand = {
  kind: "pause" | "resume" | "set_budget" | "set_price" | "skip_days" | "ad_off" | "ad_on" | "rework_ad" | "undo_last" | "stats" | "history" | "unknown";
  rubles: number | null;
  ad: { id: string; name: string } | null;
  service: string | null;
  /** Дни без показов: 1 — понедельник, 7 — воскресенье. */
  days: number[];
  wish: string;
  confirm: string;
  reply: string;
  source: "rules" | "model";
  rules: string;
  /** Остальные просьбы того же сообщения, по очереди после этой. */
  next?: ChatCommand[];
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
    siteRead: (id: number, token: string, init?: RequestInit) =>
      request<SiteRead>(`/api/site-reads/${id}`, { ...init, headers: { "X-Read-Token": token } }),
    budgetOptions: () => request<{ min: number; max: number; presets: number[] }>("/api/budget-plan/options"),
    writeAds: (profile: AdDraftProfile) =>
      request<{ id: number; token: string; status: string }>("/api/creative-sets", { method: "POST", body: JSON.stringify(profile) }),
    adSet: (id: number, token: string, init?: RequestInit) =>
      request<AdSet>(`/api/creative-sets/${id}`, { ...init, headers: { "X-Set-Token": token } }),
  };
}

export const createBusiness = (name: string, csrf: string) =>
  request<Business>("/api/businesses", { method: "POST", body: JSON.stringify({ name }) }, csrf);

export function createApi(businessId: string, csrf: string) {
  const base = `/api/businesses/${businessId}`;
  const part = encodeURIComponent;
  return {
    overview: (range?: Range | null, init?: RequestInit) => request<Overview>(`${base}/overview${period(range)}`, init),
    today: (init?: RequestInit) => request<{ rules: string; cards: TodayCard[] }>(`${base}/today`, init),
    answerTest: (decisionId: number, agree: boolean) =>
      request<{ status: string; text: string }>(`${base}/tests/offers/${decisionId}`, { method: "POST", body: JSON.stringify({ agree }) }, csrf),
    cancelTest: (id: number) => request<{ status: string; text: string }>(`${base}/tests/${id}/cancel`, { method: "POST" }, csrf),
    ads: (range?: Range | null, init?: RequestInit) => request<AdLine[]>(`${base}/ads${period(range)}`, init),
    campaign: (account: string, id: string, range?: Range | null, init?: RequestInit) =>
      request<CampaignDetail>(`${base}/campaigns/${part(account)}/${part(id)}${period(range)}`, init),
    ad: (account: string, id: string, range?: Range | null, init?: RequestInit) =>
      request<AdDetail>(`${base}/ads/${part(account)}/${part(id)}${period(range)}`, init),
    history: (init?: RequestInit) => request<HistoryLine[]>(`${base}/history`, init),
    profile: (init?: RequestInit) => request<Profile>(`${base}/profile`, init),
    saveProfile: (profile: Profile) =>
      request<SavedProfile>(`${base}/profile`, { method: "PUT", body: JSON.stringify(profile) }, csrf),
    creatives: (init?: RequestInit) => request<Board>(`${base}/creatives`, init),
    launch: (id: number) => request<CreativeReport>(`${base}/creatives/${id}/launch`, { method: "POST" }, csrf),
    rework: (account: string, adId: string, wish: string) =>
      request<{ id: number; status: "queued" }>(`${base}/ads/${part(account)}/${part(adId)}/rework`, { method: "POST", body: JSON.stringify({ wish }) }, csrf),
    reworkSet: (id: number, init?: RequestInit) => request<ReworkView>(`${base}/reworks/${id}`, init),
    chooseRework: (id: number, creativeId: number) =>
      request<ReworkView>(`${base}/reworks/${id}/choose`, { method: "POST", body: JSON.stringify({ creative_id: creativeId }) }, csrf),
    withdraw: (id: number) => request<CreativeReport>(`${base}/creatives/${id}/withdraw`, { method: "POST" }, csrf),
    accounts: (init?: RequestInit) => request<AdAccount[]>(`${base}/accounts`, init),
    balance: (login: string) => request<AccountBalance>(`${base}/accounts/${part(login)}/balance`),
    photos: (init?: RequestInit) => request<Photos>(`${base}/photos`, init),
    photoUrl: (id: number) => `${base}/photos/${id}`,
    // Файл уходит телом запроса как есть: сервер сам проверит и обработает его.
    uploadPhoto: (file: Blob, consent: boolean) =>
      request<{ id: number; url: string }>(
        `${base}/photos?consent=${consent}`,
        { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } },
        csrf,
      ),
    siteImages: (url: string) =>
      request<{ images: SiteImage[] }>(`${base}/photos/site-candidates`, { method: "POST", body: JSON.stringify({ url }) }, csrf),
    importSiteImages: (urls: string[], consent: boolean) =>
      request<{ added: number; skipped: number }>(`${base}/photos/from-site`, { method: "POST", body: JSON.stringify({ urls, consent }) }, csrf),
    archivePhoto: (id: number) => request<{ archived: boolean }>(`${base}/photos/${id}/archive`, { method: "POST" }, csrf),
    connect: (then?: "start") =>
      request<{ authorize_url: string }>(`${base}/accounts/connect${then ? `?then=${then}` : ""}`, { method: "POST" }, csrf),
    manualTokenLink: () => request<{ url: string }>(`${base}/accounts/manual-token/link`),
    saveManualToken: (login: string, token: string) =>
      request<AdAccount>(`${base}/accounts/manual-token`, { method: "POST", body: JSON.stringify({ login, token }) }, csrf),
    marking: (init?: RequestInit) => request<{ saved: MarkingView | null }>(`${base}/marking`, init),
    saveMarking: (body: MarkingInput) =>
      request<{ saved: MarkingView | null }>(`${base}/marking`, { method: "PUT", body: JSON.stringify(body) }, csrf),
    tagCheck: (init?: RequestInit) => request<TagCheckView>(`${base}/tag-check`, init),
    requestTagCheck: () => request<TagCheckView>(`${base}/tag-check`, { method: "POST" }, csrf),
    launchCheck: (init?: RequestInit) => request<LaunchCheck>(`${base}/launch-check`, init),
    startLaunch: () => request<LaunchAnswer>(`${base}/launch`, { method: "POST" }, csrf),
    adoptAds: (choice: AdsChoice) =>
      request<{ kept: number }>(`${base}/creative-sets/adopt`, { method: "POST", body: JSON.stringify(choice) }, csrf),
    wizardAds: (init?: RequestInit) => request<{ ads: WizardAd[] }>(`${base}/wizard-ads`, init),
    logout: () => request<void>("/api/auth/logout", { method: "POST" }, csrf),
    channels: (init?: RequestInit) => request<Channels>("/api/me/notifications", init),
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
    display: (init?: RequestInit) => request<Display>(`${base}/display`, init),
    command: (text: string) =>
      request<ChatCommand>(`${base}/chat/command`, { method: "POST", body: JSON.stringify({ text }) }, csrf),
    /** Дни (0 — понедельник) и часы [start, end) показа; меняется через журнал. */
    setSchedule: (days: number[], start: number, end: number, requestId: string) =>
      request<{ applied: string[] }>(`${base}/display/schedule`, { method: "POST", body: JSON.stringify({ days, start, end, request_id: requestId }) }, csrf),
    /** Не показывать в эти дни (0 — понедельник); часы каждого направления остаются его. */
    skipDays: (days: number[], requestId: string) =>
      request<{ applied: string[]; unknown: string[]; unchanged: string[] }>(`${base}/display/skip-days`, { method: "POST", body: JSON.stringify({ days, request_id: requestId }) }, csrf),
    setRegions: (regions: number[], requestId: string) =>
      request<{ applied: string[] }>(`${base}/display/regions`, { method: "POST", body: JSON.stringify({ regions, request_id: requestId }) }, csrf),
    readSite: (url: string) =>
      request<{ id: number; status: string; url: string }>(`${base}/site-reads`, { method: "POST", body: JSON.stringify({ url }) }, csrf),
    siteRead: (init?: RequestInit) => request<SiteRead | null>(`${base}/site-reads/latest`, init),
    budgetPlan: (init?: RequestInit) => request<{ rubles: number | null }>(`${base}/budget-plan`, init),
    saveBudgetPlan: (rubles: number) =>
      request<{ rubles: number }>(`${base}/budget-plan`, { method: "PUT", body: JSON.stringify({ rubles }) }, csrf),
    // Режим бюджета владельца хранится на сервере: общий пул делится по сайтам там же.
    budgetPortfolio: (init?: RequestInit) => request<BudgetPortfolio>("/api/me/budget-portfolio", init),
    saveBudgetPortfolio: (body: { mode: "separate" } | { mode: "shared"; rubles: number }) =>
      request<PortfolioSaved>("/api/me/budget-portfolio", { method: "PUT", body: JSON.stringify(body) }, csrf),
    setGoal: (rubles: number) =>
      request<{ goal: number }>(`${base}/goal`, { method: "POST", body: JSON.stringify({ rubles }) }, csrf),
    answerClients: (range: Range, clients: number) =>
      request<ClientAnswer>(`${base}/clients`, { method: "POST", body: JSON.stringify({ ...range, clients }) }, csrf),
    rollbackPreview: (changeId: number, init?: RequestInit) => request<UndoPreview>(`${base}/changes/${changeId}/rollback`, init),
    rollback: (changeId: number, whole = false) =>
      request<{ status: string }>(`${base}/changes/${changeId}/rollback${whole ? "?whole=true" : ""}`, { method: "POST" }, csrf),
    confirmBalance: (account: string) =>
      request<{ confirmed: boolean }>(
        `${base}/balance/confirm`,
        { method: "POST", body: JSON.stringify({ account }) },
        csrf,
      ),
  };
}

export type Api = ReturnType<typeof createApi>;

export type AdvertiserKind = "legal" | "ip" | "person";
export type MarkingInput = { kind: AdvertiserKind; inn: string; name: string; phone?: string; okved?: string };
/** ИНН и телефон приходят только с последними цифрами. */
export type MarkingView = { kind: AdvertiserKind; inn: string; name: string; phone: string | null; okved: string | null };
export type TagCheck = { status: "queued" | "running" | "found" | "missing" | "failed"; url: string; counters: number[]; problem: string | null };
export type TagCheckView = { check: TagCheck | null; site: string };
export type LaunchItem = { key: "profile" | "plan" | "account" | "marking" | "counter" | "ads" | "launch_plan"; ok: boolean; text: string };
export type LaunchCheck = {
  items: LaunchItem[];
  ready: boolean;
  launch_available: boolean;
  launched?: "done" | "pending" | "refused" | null;
  launch_text: string;
};
export type ReworkVariant = { id: number; headline: string; text: string; state: string; sources: { fragment: string; label: string; key: string }[] };
export type ReworkView = { id: number; status: "queued" | "ready" | "failed"; ad_id: string; problem: string | null; variants: ReworkVariant[]; text?: string };
export type LaunchAnswer = { status: "done" | "pending"; campaigns: { name: string; limit_rubles: number }[]; text: string };

export async function openSession(): Promise<{ me: Me; businesses: Business[] } | null> {
  try {
    const me = await request<Me>("/api/me");
    const businesses = await request<Business[]>("/api/businesses");
    return { me, businesses };
  } catch {
    // Нет сервиса или входа: открываем запуск с первого шага.
    return null;
  }
}
