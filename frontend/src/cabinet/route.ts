// Адрес экрана в хэше: «#campaigns/логин/101?since=2026-11-01&until=2026-11-10».
// Так работают кнопка «Назад» и ссылка на конкретный отчёт.
import type { Range } from "../api";

export type Section = "home" | "campaigns" | "stats" | "ads" | "history" | "profile" | "notify";
export type Route =
  | { section: Section }
  | { section: "campaign"; account: string; id: string }
  | { section: "ad"; account: string; id: string }
  | { section: "creative"; id: string };

const SECTIONS: Section[] = ["home", "campaigns", "stats", "ads", "history", "profile", "notify"];
const DAY = /^\d{4}-\d{2}-\d{2}$/;

export function parseHash(hash: string): { route: Route; range: Range | null } {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").map(decodeURIComponent);
  const [head, account = "", id = ""] = parts;
  const params = new URLSearchParams(query);
  const since = params.get("since");
  const until = params.get("until");
  const range = since && until && DAY.test(since) && DAY.test(until) && since <= until ? { since, until } : null;
  let route: Route = { section: "home" };
  if (head === "campaigns" && parts.length === 3) route = { section: "campaign", account, id };
  else if (head === "ad" && parts.length === 3) route = { section: "ad", account, id };
  else if (head === "creative" && parts.length === 2 && /^\d+$/.test(account)) route = { section: "creative", id: account };
  else if (SECTIONS.includes(head as Section)) route = { section: head as Section };
  return { route, range };
}

export function formatHash(route: Route, range: Range | null): string {
  const part = encodeURIComponent;
  let path: string = route.section;
  if (route.section === "campaign") path = `campaigns/${part(route.account)}/${part(route.id)}`;
  if (route.section === "ad") path = `ad/${part(route.account)}/${part(route.id)}`;
  if (route.section === "creative") path = `creative/${part(route.id)}`;
  return `#${path}${range ? `?since=${range.since}&until=${range.until}` : ""}`;
}

// Раздел меню, который подсвечиваем: отчёты направления и объявления живут в «Рекламе»,
// а объявление, которое ещё готовится, — в «Объявлениях».
export function navSection(route: Route): Section {
  if (route.section === "creative") return "ads";
  return route.section === "campaign" || route.section === "ad" ? "campaigns" : route.section;
}

export function campaignHref(account: string, id: string, range: Range | null): string {
  return formatHash({ section: "campaign", account, id }, range);
}

export function adHref(account: string, id: string, range: Range | null): string {
  return formatHash({ section: "ad", account, id }, range);
}

export function creativeHref(id: number, range: Range | null): string {
  return formatHash({ section: "creative", id: String(id) }, range);
}
