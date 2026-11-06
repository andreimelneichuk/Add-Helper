import { z } from "zod";

// Проверки формы в одном месте. Сервис всё равно проверяет данные заново: здесь только быстрый отказ с понятным словом.

/** ИНН: 10 цифр у организации, 12 у предпринимателя и физлица. Контрольную сумму проверяет сервис. */
export const InnSchema = z.string().regex(/^(\d{10}|\d{12})$/u, "ИНН — 10 или 12 цифр.");

/** Услуга профиля: название не пустое, цена либо не задана, либо больше нуля. */
export const ServiceSchema = z.object({
  name: z.string().trim().min(1),
  price: z.number().positive("Цена должна быть больше нуля.").nullable(),
});

/** План бюджета: целые рубли в месяц, больше нуля. */
export const BudgetPlanSchema = z.number({ invalid_type_error: "Введите сумму." }).int("Сумма — целое число рублей.").positive("Сумма должна быть больше нуля.");

export const isInn = (value: string): boolean => InnSchema.safeParse(value).success;

/** Первая причина отказа словами владельца или null. */
export function planProblem(value: number): string | null {
  const parsed = BudgetPlanSchema.safeParse(value);
  return parsed.success ? null : parsed.error.issues[0]?.message ?? "Проверьте сумму.";
}
