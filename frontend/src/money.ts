// Деньги хранятся целыми микроединицами, как на площадке. Округляем только при показе.
export const MICROS_PER_RUBLE = 1_000_000;

export function rublesToMicros(rubles: number): number {
  return Math.trunc(rubles) * MICROS_PER_RUBLE;
}

export function formatRubles(micros: number): string {
  const rubles = Math.round(micros / MICROS_PER_RUBLE);
  return `${rubles.toLocaleString("ru-RU").replace(/ /g, " ")} ₽`;
}
