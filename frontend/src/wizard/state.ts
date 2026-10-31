import { rublesToMicros } from "../money";
import type { InnKind } from "../inn";

export const STEPS = [
  "login",
  "account",
  "business",
  "marking",
  "tracking",
  "budget",
  "price",
  "ads",
  "launch",
] as const;
export type Step = (typeof STEPS)[number];

export const STORAGE_KEY = "adhelper.prototype.wizard";
// Условные значения прототипа. В сервисе их задаст версионируемый файл правил.
export const LOW_BUDGET_RUBLES = 10_000;
export const SUGGESTED_PRICE_SHARE_PERCENT = 30;

export interface WizardState {
  step: Step;
  consent: boolean;
  accountConnected: boolean;
  site: string;
  businessName: string;
  service: string;
  servicePrice: string;
  address: string;
  feature: string;
  innKind: InnKind;
  legalName: string;
  /** ИНН живёт только в памяти: реквизиты нельзя оставлять в браузере открытым текстом. */
  inn: string;
  trackingChecked: boolean;
  budget: string;
  averageCheck: string;
  clientPrice: string;
  rejectedAds: string[];
  launched: boolean;
}

export const INITIAL: WizardState = {
  step: "login",
  consent: false,
  accountConnected: false,
  site: "",
  businessName: "",
  service: "",
  servicePrice: "",
  address: "",
  feature: "",
  innKind: "person",
  legalName: "",
  inn: "",
  trackingChecked: false,
  budget: "30000",
  averageCheck: "",
  clientPrice: "",
  rejectedAds: [],
  launched: false,
};

export function loadState(): WizardState {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? { ...INITIAL, ...(JSON.parse(saved) as Partial<WizardState>), inn: "" } : INITIAL;
  } catch {
    return INITIAL;
  }
}

export function saveState(state: WizardState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...state, inn: "" }));
  } catch {
    // Без хранилища мастер работает, только не продолжится после перезагрузки.
  }
}

export function nextStep(step: Step): Step {
  return STEPS[STEPS.indexOf(step) + 1] ?? step;
}

export function previousStep(step: Step): Step {
  return STEPS[STEPS.indexOf(step) - 1] ?? step;
}

export function suggestedClientPrice(averageCheck: number): number {
  return Math.floor((averageCheck * SUGGESTED_PRICE_SHARE_PERCENT) / 100);
}

export interface Forecast {
  low: number;
  high: number;
}

/** Грубая оценка из бюджета и допустимой цены клиента, а не обещание. */
export function forecast(budgetRubles: number, clientPriceRubles: number): Forecast | null {
  if (budgetRubles <= 0 || clientPriceRubles <= 0) return null;
  const budget = rublesToMicros(budgetRubles);
  const price = rublesToMicros(clientPriceRubles);
  return { low: Math.floor(budget / (price * 2)), high: Math.floor(budget / price) };
}

export interface AdCard {
  id: string;
  title: string;
  text: string;
}

/** В объявлениях только факты, которые ввёл владелец. Других цифр и обещаний тут нет. */
export function adsFromProfile(state: WizardState): AdCard[] {
  const service = state.service.trim();
  const price = state.servicePrice.trim();
  const address = state.address.trim();
  const feature = state.feature.trim();
  const name = state.businessName.trim();
  const cards: AdCard[] = [
    { id: "name", title: `${service} в «${name}»`, text: address ? `Ждём вас: ${address}.` : "Запишитесь онлайн." },
  ];
  if (price) {
    cards.push({ id: "price", title: `${service} за ${price} ₽`, text: `«${name}». Запишитесь на удобное время.` });
  }
  if (feature) {
    cards.push({ id: "feature", title: `${service}: ${feature}`, text: `«${name}». Запишитесь на удобное время.` });
  }
  return cards;
}
