import { describe, expect, it } from "vitest";
import { STEP, STEPS } from "./data";

describe("шаги мастера названы", () => {
  it("имя шага указывает на свой шаг в списке", () => {
    expect(STEPS[STEP.site]!.label).toBe("О бизнесе");
    expect(STEPS[STEP.profile]!.label).toBe("Профиль");
    expect(STEPS[STEP.budget]!.label).toBe("Бюджет");
    expect(STEPS[STEP.ads]!.label).toBe("Реклама");
    expect(STEPS[STEP.cabinet]!.label).toBe("Кабинет");
    expect(STEPS[STEP.marking]!.label).toBe("Маркировка");
    expect(STEPS[STEP.stats]!.label).toBe("Статистика");
    expect(STEPS[STEP.launch]!.label).toBe("Запуск");
  });

  it("запуск последний, номера идут подряд", () => {
    expect(STEP.launch).toBe(STEPS.length - 1);
    expect(Object.values(STEP)).toEqual([...Array(STEPS.length).keys()]);
  });
});
