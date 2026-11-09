/** Автопилот поменяли (настройки или чат): плашка в шапке перечитывает его положение. */
export const AUTOPILOT_CHANGED = "ah-autopilot-changed";

export function announceAutopilotChange(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTOPILOT_CHANGED));
}
