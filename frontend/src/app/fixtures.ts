import type { Ad, Day, Fact, HistoryEntry, Period, PeriodStats, SettingKey, Situation } from "./data";
import type { State } from "./store";

/**
 * Подставные данные прототипа. В боевой сборке пусты: их ставит `installFixtures` из `src/mock`
 * (только `/?mock` в сборке для разработки и тесты), поэтому в `dist` их нет.
 */
export type Fixtures = {
  DAYS: Record<Situation, Day[]>;
  ACCOUNT: Record<Situation, number>;
  OPENING: Record<Situation, string>;
  ADS: Ad[];
  STATS: Record<Period, PeriodStats>;
  OPTIONS: Record<SettingKey, { label: string; values: (string | number)[] }>;
  FACTS: Fact[];
  /** Тексты и значения прототипа, которых нет в настоящих экранах. */
  TEXT: { area: string; audience: string; rewind: string; rejected: string; profile: (price: number | null) => [string, string][] };
  histFor: (id: Situation) => HistoryEntry[];
  draftAd: (s: State, base: Ad | null, ask: string, attempt?: number) => { ad: Ad } | { refusal: string };
};

const SITUATIONS: Situation[] = ["running", "first", "problem", "verdict", "budget", "rejected", "access"];
const byKey = <T,>(keys: readonly string[], make: () => T) => Object.fromEntries(keys.map((k) => [k, make()])) as Record<string, T>;
const emptyStats = (): PeriodStats => ({ label: "", cost: 0, leads: 0, shows: 0, clicks: 0, dirs: [] });

export const fixtures: Fixtures = {
  DAYS: byKey(SITUATIONS, () => [] as Day[]) as Fixtures["DAYS"],
  ACCOUNT: byKey(SITUATIONS, () => 0) as Fixtures["ACCOUNT"],
  OPENING: byKey(SITUATIONS, () => "") as Fixtures["OPENING"],
  ADS: [],
  STATS: { week: emptyStats(), month: emptyStats() },
  OPTIONS: byKey(["services", "area", "hours", "goal", "budget"], () => ({ label: "", values: [] })) as Fixtures["OPTIONS"],
  FACTS: [],
  histFor: () => [],
  TEXT: { area: "", audience: "", rewind: "", rejected: "", profile: () => [] },
  draftAd: () => ({ refusal: "Автор объявлений недоступен." }),
};

export const installFixtures = (f: Fixtures): void => void Object.assign(fixtures, f);
