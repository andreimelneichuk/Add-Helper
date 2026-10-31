const WEIGHTS_10 = [2, 4, 10, 3, 5, 9, 4, 6, 8];
const WEIGHTS_11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
const WEIGHTS_12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8];

function checkDigit(digits: number[], weights: number[]): number {
  const sum = weights.reduce((total, weight, index) => total + weight * (digits[index] ?? 0), 0);
  return (sum % 11) % 10;
}

export type InnKind = "company" | "person";

/** Контрольные разряды ИНН: 10 цифр у организации, 12 у ИП и физического лица. */
export function isValidInn(value: string, kind: InnKind): boolean {
  if (!/^\d+$/.test(value)) return false;
  const digits = [...value].map(Number);
  if (kind === "company") {
    return digits.length === 10 && checkDigit(digits, WEIGHTS_10) === digits[9];
  }
  return (
    digits.length === 12 &&
    checkDigit(digits, WEIGHTS_11) === digits[10] &&
    checkDigit(digits, WEIGHTS_12) === digits[11]
  );
}
