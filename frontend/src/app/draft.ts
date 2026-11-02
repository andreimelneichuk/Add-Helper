// Черновик запуска до входа: живёт в браузере, после входа становится бизнесом, профилем и планом бюджета.
import type { Business, Me, Profile } from "../api";
import type { Fact } from "./data";

export type StartDraft = {
  site: string;
  facts: Fact[];
  budget: number;
  /** Бизнес, уже созданный этим черновиком: повтор после сбоя не создаёт второй. */
  businessId?: string;
};

export type TransferDeps = {
  session: () => Promise<{ me: Me; businesses: Business[] } | null>;
  createBusiness: (name: string, csrf: string) => Promise<Business>;
  business: (id: string, csrf: string) => {
    saveProfile: (profile: Profile) => Promise<unknown>;
    saveBudgetPlan: (rubles: number) => Promise<unknown>;
  };
};

export type Transfer =
  | { status: "done"; email: string | null; businessId: string }
  | { status: "exists"; name: string }
  | { status: "signed_out" };

const KEY = "adhelper.startDraft";
const MAX_SERVICES = 30;
const MAX_FEATURES = 20;

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
  try { window.localStorage.removeItem(KEY); } catch { /* без хранилища */ }
}

// Цену в карточку услуги ставит сам мастер в виде «Название, 1 500 ₽»; так же её и отделяем.
const PRICED = /^(.*\S),\s*(\d[\d\s]*)\s*₽$/u;

function service(value: string): Profile["services"][number] {
  const m = PRICED.exec(value.trim());
  return m ? { name: m[1]!, price: Number(m[2]!.replace(/\s/gu, "")) } : { name: value.trim(), price: null };
}

export function draftProfile(draft: StartDraft): Profile {
  const of = (field: Fact["field"]) => draft.facts.filter((f) => f.field === field).map((f) => f.value.trim()).filter(Boolean);
  // Факт, добавленный словами, без поля: в рекламе он пойдёт как особенность, которую подтвердил владелец.
  const free = draft.facts.filter((f) => !f.field).map((f) => f.value.trim()).filter(Boolean);
  return {
    niche: of("niche")[0] ?? "",
    address: of("address")[0] ?? "",
    services: of("service").slice(0, MAX_SERVICES).map(service),
    features: [...of("feature"), ...free].slice(0, MAX_FEATURES),
    website: draft.site.trim(),
  };
}

export function businessName(draft: StartDraft): string {
  try {
    if (draft.site.trim()) return new URL(draft.site.includes("://") ? draft.site : `https://${draft.site}`).hostname.replace(/^www\./u, "");
  } catch { /* не адрес: берём вид деятельности */ }
  const niche = draftProfile(draft).niche;
  return niche ? niche[0]!.toUpperCase() + niche.slice(1) : "Мой бизнес";
}

/** Перенести черновик после входа. Профиль чужого бизнеса не трогаем: у владельца он уже есть. */
export async function transferDraft(deps: TransferDeps, draft: StartDraft): Promise<Transfer> {
  const session = await deps.session();
  if (!session) return { status: "signed_out" };
  const csrf = session.me.csrf_token;
  let id = draft.businessId && session.businesses.some((b) => b.id === draft.businessId) ? draft.businessId : null;
  if (!id) {
    const existing = session.businesses[0];
    if (existing) {
      clearDraft();
      return { status: "exists", name: existing.name };
    }
    id = (await deps.createBusiness(businessName(draft), csrf)).id;
    saveDraft({ ...draft, businessId: id });
  }
  const api = deps.business(id, csrf);
  await api.saveProfile(draftProfile(draft));
  await api.saveBudgetPlan(draft.budget);
  clearDraft();
  return { status: "done", email: session.me.email, businessId: id };
}
