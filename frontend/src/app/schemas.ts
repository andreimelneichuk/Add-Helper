import * as v from "valibot";

// Проверки формы в одном месте. Сервис всё равно проверяет данные заново: здесь только быстрый отказ с понятным словом.

/** ИНН: 10 цифр у организации, 12 у предпринимателя и физлица. Контрольную сумму проверяет сервис. */
export const InnSchema = v.pipe(v.string(), v.regex(/^(\d{10}|\d{12})$/u, "ИНН — 10 или 12 цифр."));

/** Цена услуги: не задана или больше нуля. */
export const PriceSchema = v.nullable(v.pipe(v.number(), v.gtValue(0, "Цена должна быть больше нуля.")));

/** Услуга профиля: название не пустое, цена либо не задана, либо больше нуля. */
export const ServiceSchema = v.object({
  name: v.pipe(v.string(), v.trim(), v.minLength(1)),
  price: PriceSchema,
});

/** План бюджета: целые рубли в месяц, больше нуля. */
export const BudgetPlanSchema = v.pipe(
  v.number("Введите сумму."),
  v.integer("Сумма — целое число рублей."),
  v.gtValue(0, "Сумма должна быть больше нуля."),
);

export const isInn = (value: string): boolean => v.safeParse(InnSchema, value).success;

/** Первая причина отказа словами владельца или null. */
export function planProblem(value: number): string | null {
  const parsed = v.safeParse(BudgetPlanSchema, value);
  return parsed.success ? null : parsed.issues[0]?.message ?? "Проверьте сумму.";
}
