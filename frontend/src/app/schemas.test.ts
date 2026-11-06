import { describe, expect, it } from "vitest";
import * as v from "valibot";
import { InnSchema, isInn, planProblem, ServiceSchema } from "./schemas";

describe("схемы форм", () => {
  it("ИНН — 10 или 12 цифр", () => {
    expect(isInn("7707083893")).toBe(true);
    expect(isInn("500100732259")).toBe(true);
    expect(isInn("770708389")).toBe(false);
    expect(isInn("77070838930")).toBe(false);
    expect(isInn("77070838a3")).toBe(false);
    expect(v.safeParse(InnSchema, "12").success).toBe(false);
  });

  it("план бюджета — целые рубли больше нуля", () => {
    expect(planProblem(20000)).toBeNull();
    expect(planProblem(0)).toBe("Сумма должна быть больше нуля.");
    expect(planProblem(Number.NaN)).toBe("Введите сумму.");
    expect(planProblem(10.5)).toBe("Сумма — целое число рублей.");
  });

  it("услуга: цена задана или пуста, но не ноль", () => {
    expect(v.safeParse(ServiceSchema, { name: "Маникюр", price: null }).success).toBe(true);
    expect(v.safeParse(ServiceSchema, { name: "Маникюр", price: 0 }).success).toBe(false);
    expect(v.safeParse(ServiceSchema, { name: " ", price: 100 }).success).toBe(false);
  });
});
