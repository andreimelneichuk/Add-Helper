import { describe, expect, it } from "vitest";
import { cityAdvice, mainCityName, withMainCityFirst } from "./metricaCity";

const CITIES = { "1": 109077778, "3": 109077486 };

describe("подсказка про счётчик города", () => {
  it("счётчиков ещё нет: предлагает добавить городской", () => {
    const advice = cityAdvice(CITIES, "1", []);
    expect(advice.canUseMainCity).toBe(true);
    expect(advice.text).toContain("Добавьте его");
    expect(advice.text).not.toContain("по общему");
  });

  it("основным стоит общий счётчик: обещает точный городской", () => {
    const advice = cityAdvice(CITIES, "1", [95375303]);
    expect(advice.canUseMainCity).toBe(true);
    expect(advice.primaryIsCity).toBe(false);
    expect(advice.text).toContain("по общему");
  });

  it("основным стоит счётчик другого города: не называет его общим", () => {
    const advice = cityAdvice(CITIES, "1", [109077486]);
    expect(advice.canUseMainCity).toBe(true);
    expect(advice.primaryIsCity).toBe(true);
    expect(advice.text).toContain("другого города");
    expect(advice.text).not.toContain("по общему");
  });

  it("город уже основной: предлагать нечего", () => {
    const advice = cityAdvice(CITIES, "1", [109077778]);
    expect(advice.canUseMainCity).toBe(false);
    expect(advice.text).toBe("");
  });

  it("сайт не сообщает город по умолчанию: только объяснение", () => {
    const advice = cityAdvice(CITIES, null, [95375303]);
    expect(advice.hasOtherCities).toBe(true);
    expect(advice.canUseMainCity).toBe(false);
    expect(advice.text).toContain("не сообщает");
  });

  it("город известен, а счётчика для него нет: говорит об этом, а не о незнании", () => {
    const advice = cityAdvice({ "3": 109077486 }, "1", [95375303]);
    expect(advice.cityCounterMissing).toBe(true);
    expect(advice.text).toContain("Для вашего города счётчик на сайте не нашёлся");
    expect(advice.text).not.toContain("не сообщает");
  });

  it("городских счётчиков нет: молчит", () => {
    const advice = cityAdvice({}, "1", [95375303]);
    expect(advice).toMatchObject({ mainCounter: null, canUseMainCity: false, hasOtherCities: false, text: "" });
  });

  it("сравнивает город по умолчанию как число, а не как строку", () => {
    expect(cityAdvice({ "01": 109077778 }, "1", []).mainCounter).toBe(109077778);
    expect(cityAdvice(CITIES, "01", []).mainCounter).toBe(109077778);
  });

  it("нечисловой ключ города ничего не ломает", () => {
    const advice = cityAdvice({ ekb: 109077778 }, "1", [95375303]);
    expect(advice.mainCounter).toBeNull();
    expect(advice.hasOtherCities).toBe(true);
    expect(advice.text).toContain("Для вашего города счётчик на сайте не нашёлся");
  });
});

describe("как поставить городской счётчик основным", () => {
  const items = [
    { id: 95375303, name: "Общий", primary: true },
    { id: 109077778, name: "Городской", primary: false },
    { id: 54127669 },
  ];

  it("городской встаёт первым и основным, остальные остаются резервными с названиями", () => {
    expect(withMainCityFirst(items, 109077778, "Городской")).toEqual([
      { id: 109077778, name: "Городской", primary: true },
      { id: 95375303, name: "Общий", primary: false },
      { id: 54127669, primary: false },
    ]);
  });

  it("счётчика ещё нет в списке — добавляет его в начало", () => {
    expect(withMainCityFirst(items, 11111111, "Мой город")[0]).toEqual({
      id: 11111111,
      name: "Мой город",
      primary: true,
    });
  });

  it("набранное название важнее сохранённого, очищенное остаётся пустым", () => {
    expect(mainCityName({ "1": " Городской " }, { "1": "Старое" }, 1)).toBe("Городской");
    expect(mainCityName({ "1": "" }, { "1": "Старое" }, 1)).toBeUndefined();
    expect(mainCityName({}, { "1": "Старое" }, 1)).toBe("Старое");
    expect(mainCityName({}, { "1": "   " }, 1)).toBe("Мой город");
    expect(mainCityName({}, {}, 1)).toBe("Мой город");
  });
});
