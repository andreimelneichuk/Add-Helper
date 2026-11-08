// Черновик запуска до входа: живёт в браузере, после входа становится бизнесом, профилем и планом бюджета.
import { ApiError } from "../api";
import type { AdsChoice, Business, Me, Profile } from "../api";
import type { Fact } from "./data";

export type StartDraft = {
  site: string;
  facts: Fact[];
  budget: number;
  /** Бизнес, уже созданный этим черновиком: повтор после сбоя не создаёт второй. */
  businessId?: string;
  /** Объявления, которые владелец оставил на шаге «Объявления». */
  ads?: AdsChoice;
  /** Шаг мастера, на котором остановился владелец. */
  step?: number;
  /** Выключенные черновики объявлений. */
  draftOff?: Record<string, boolean>;
  /** Пользовательские или отредактированные объявления. */
  customAds?: { id: string; headline: string; text: string; facts: string[] }[];
  /** Создать как новый бизнес, даже если у пользователя уже есть бизнесы. */
  createAsNew?: boolean;
};

export type TransferDeps = {
  session: () => Promise<{ me: Me; businesses: Business[] } | null>;
  createBusiness: (name: string, csrf: string) => Promise<Business>;
  business: (id: string, csrf: string) => {
    saveProfile: (profile: Profile) => Promise<unknown>;
    saveBudgetPlan: (rubles: number) => Promise<unknown>;
    adoptAds?: (choice: AdsChoice) => Promise<unknown>;
  };
};

export type Transfer =
  | { status: "done"; email: string | null; businessId: string; csrf: string }
  | { status: "exists"; name: string }
  | { status: "signed_out" };

const KEY = "adhelper.startDraft";
const SITE_KEY = "adhelper.startSite";
const READING_KEY = "adhelper.startReading";
const MAX_SERVICES = 30;
const MAX_FEATURES = 20;

export type PendingRead = { id: number; token: string; url: string };

export function saveStartSite(site: string) {
  try {
    if (site.trim()) {
      window.localStorage.setItem(SITE_KEY, site.trim());
    } else {
      window.localStorage.removeItem(SITE_KEY);
    }
  } catch { /* без хранилища */ }
}

export function loadStartSite(): string {
  try { return window.localStorage.getItem(SITE_KEY) ?? ""; } catch { return ""; }
}

export function clearStartSite() {
  try { window.localStorage.removeItem(SITE_KEY); } catch { /* без хранилища */ }
}

export function saveStartReading(reading: PendingRead) {
  try { window.localStorage.setItem(READING_KEY, JSON.stringify(reading)); } catch { /* без хранилища */ }
}

export function loadStartReading(): PendingRead | null {
  try {
    const raw = window.localStorage.getItem(READING_KEY);
    return raw ? (JSON.parse(raw) as PendingRead) : null;
  } catch {
    return null;
  }
}

export function clearStartReading() {
  try { window.localStorage.removeItem(READING_KEY); } catch { /* без хранилища */ }
}

// Хранилище может быть недоступно (приватный режим): тогда черновик просто не переживёт вход.
export function saveDraft(draft: StartDraft) {
  try { window.localStorage.setItem(KEY, JSON.stringify(draft)); } catch { /* без хранилища */ }
}
export function loadDraft(): StartDraft | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as StartDraft) : null;
  } catch {
    return null;
  }
}
export function clearDraft() {
  try {
    window.localStorage.removeItem(KEY);
    window.localStorage.removeItem(SITE_KEY);
    window.localStorage.removeItem(READING_KEY);
  } catch { /* без хранилища */ }
}

// Цену в карточку услуги ставит сам мастер в виде «Название, 1 500 ₽»; так же её и отделяем.
// Бизнес, который ведёт мастер после входа: нужен, когда владелец вернётся с подключения кабинета.
const WIZARD_KEY = "adhelper.startBusiness";

export function saveWizard(businessId: string) {
  try { window.localStorage.setItem(WIZARD_KEY, businessId); } catch { /* без хранилища */ }
}
export function loadWizard(): string | null {
  try { return window.localStorage.getItem(WIZARD_KEY); } catch { return null; }
}
export function clearWizard() {
  try { window.localStorage.removeItem(WIZARD_KEY); } catch { /* без хранилища */ }
}

const PRICED = /^(.*?)(?:,\s*|\s*[-–—]\s*|\s+)(\d[\d\s]*)\s*(?:₽|р\.?|руб\.?)?$/ui;

function service(value: string): Profile["services"][number] {
  const m = PRICED.exec(value.trim());
  return m && m[2] ? { name: (m[1] || value).trim(), price: Number(m[2].replace(/\s/gu, "")) } : { name: value.trim(), price: null };
}

export function draftProfile(draft: StartDraft): Profile {
  const of = (field: Fact["field"]) => draft.facts.filter((f) => f.field === field).map((f) => f.value.trim()).filter(Boolean);
  // Факт, добавленный словами, без поля: в рекламе он пойдёт как особенность, которую подтвердил владелец.
  const free = draft.facts.filter((f) => !f.field).map((f) => f.value.trim()).filter(Boolean);
  let services = of("service").slice(0, MAX_SERVICES).map(service);
  let features = [...of("feature"), ...free].slice(0, MAX_FEATURES);
  if (services.length === 0 && free.length > 0) {
    services = [service(free[0]!)];
    features = [...of("feature"), ...free.slice(1)].slice(0, MAX_FEATURES);
  }
  return {
    niche: of("niche")[0] ?? "",
    address: of("address")[0] ?? "",
    services,
    features,
    website: draft.site.trim(),
  };
}

export function businessName(draft: StartDraft): string {
  try {
    if (draft.site.trim()) return new URL(draft.site.includes("://") ? draft.site : `https://${draft.site}`).hostname.replace(/^www\./u, "");
  } catch { /* не адрес: берём вид деятельности */ }
  const niche = draftProfile(draft).niche;
  if (niche) return niche[0]!.toUpperCase() + niche.slice(1);
  const firstService = draftProfile(draft).services[0]?.name;
  if (firstService) return firstService[0]!.toUpperCase() + firstService.slice(1);
  return "Мой бизнес";
}

/** Перенести черновик после входа. Профиль чужого бизнеса не трогаем: у владельца он уже есть. */
export async function transferDraft(deps: TransferDeps, draft: StartDraft): Promise<Transfer> {
  const session = await deps.session();
  if (!session) return { status: "signed_out" };
  const csrf = session.me.csrf_token;
  let id = draft.businessId && session.businesses.some((b) => b.id === draft.businessId) ? draft.businessId : null;
  if (!id) {
    const existing = session.businesses[0];
    if (existing && !draft.createAsNew) {
      clearDraft();
      return { status: "exists", name: existing.name };
    }
    id = (await deps.createBusiness(businessName(draft), csrf)).id;
    saveDraft({ ...draft, businessId: id });
  }
  try { window.localStorage.setItem("adhelper.activeBusinessId", id); } catch { /* без хранилища */ }
  const api = deps.business(id, csrf);
  await api.saveProfile(draftProfile(draft));
  await api.saveBudgetPlan(draft.budget);
  if (draft.ads && api.adoptAds) {
    try {
      await api.adoptAds(draft.ads);
    } catch (e) {
      // Набор пропал или ещё не готов: вход важнее, объявления напишутся заново к запуску.
      if (!(e instanceof ApiError) || e.status >= 500) throw e;
    }
  }
  if (draft.customAds && draft.customAds.length > 0) {
    for (const ca of draft.customAds) {
      if (ca.id.startsWith("custom-") && (api as any).createWizardAd) {
        try {
          await (api as any).createWizardAd({ headline: ca.headline, text: ca.text });
        } catch { /* без ошибки мастера */ }
      } else if ((ca.id.startsWith("w") || ca.id.startsWith("draft-")) && (api as any).updateWizardAd) {
        try {
          const wId = ca.id.startsWith("w") ? ca.id : ca.id.replace("draft-", "w");
          await (api as any).updateWizardAd(wId, { headline: ca.headline, text: ca.text });
        } catch { /* без ошибки мастера */ }
      }
    }
  }
  clearDraft();
  saveWizard(id);
  return { status: "done", email: session.me.email, businessId: id, csrf };
}
