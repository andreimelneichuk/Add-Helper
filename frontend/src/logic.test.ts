import { describe, expect, it } from "vitest";
import { isValidInn } from "./inn";
import { formatRubles, rublesToMicros } from "./money";
import { HISTORY, applyRollback } from "./testData";
import { INITIAL, adsFromProfile, forecast, suggestedClientPrice } from "./wizard/state";

describe("деньги", () => {
  it("хранятся целыми микроединицами, округляются при показе", () => {
    expect(rublesToMicros(1500)).toBe(1_500_000_000);
    expect(formatRubles(1_500_400_000)).toBe("1 500 ₽");
  });
});

describe("ИНН", () => {
  it("проверяет контрольные разряды", () => {
    expect(isValidInn("7707083893", "company")).toBe(true);
    expect(isValidInn("7707083894", "company")).toBe(false);
    expect(isValidInn("500100732259", "person")).toBe(true);
    expect(isValidInn("500100732258", "person")).toBe(false);
    expect(isValidInn("7707083893", "person")).toBe(false);
    expect(isValidInn("abc", "company")).toBe(false);
  });
});

describe("оценки", () => {
  it("предлагают цену клиента и диапазон целыми числами", () => {
    expect(suggestedClientPrice(2500)).toBe(750);
    expect(forecast(30000, 750)).toEqual({ low: 20, high: 40 });
    expect(forecast(30000, 0)).toBeNull();
  });
});

describe("объявления", () => {
  it("содержат только факты из профиля", () => {
    const state = { ...INITIAL, businessName: "Лак", service: "Маникюр" };
    const cards = adsFromProfile(state);
    expect(cards).toHaveLength(1);
    expect(cards.map((card) => card.title + card.text).join()).not.toMatch(/\d/);
  });
  it("показывают цену, только если её указали", () => {
    const state = { ...INITIAL, businessName: "Лак", service: "Маникюр", servicePrice: "1500" };
    expect(adsFromProfile(state).map((card) => card.title)).toContain("Маникюр за 1500 ₽");
  });
});

describe("отмена", () => {
  it("добавляет запись и не стирает исходную", () => {
    const result = applyRollback(HISTORY, 1);
    expect(result).toHaveLength(HISTORY.length + 1);
    expect(result.find((entry) => entry.id === 1)?.reverted).toBe(true);
    expect(result[0]?.isRollback).toBe(true);
  });
  it("при зависимом изменении отменяет и его", () => {
    const result = applyRollback(HISTORY, 2);
    expect(result.find((entry) => entry.id === 3)?.reverted).toBe(true);
    expect(result.find((entry) => entry.id === 1)?.reverted).toBeUndefined();
    expect(result).toHaveLength(HISTORY.length + 2);
  });
  it("не отменяет защитную остановку и повторную отмену", () => {
    expect(applyRollback(HISTORY, 0)).toBe(HISTORY);
    const once = applyRollback(HISTORY, 1);
    expect(applyRollback(once, 1)).toBe(once);
  });
});
