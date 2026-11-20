/** Подсказка про счётчик города: что сказать владельцу и можно ли предложить действие.
 *
 * Коды счётчиков владельцу не показываем — он принимает решение по смыслу, а не по номеру.
 * Логика общая для настроек и мастера, чтобы тексты не разъехались.
 */

export const MAIN_CITY_NAME = "Мой город";

export type CounterItem = { id: number; name?: string; primary?: boolean };

export type CityAdvice = {
  /** Счётчик города по умолчанию; null — сайт его не сообщил или для города счётчика нет. */
  mainCounter: number | null;
  /** Можно предложить владельцу считать визиты по своему городу. */
  canUseMainCity: boolean;
  /** Городские счётчики есть, но какой из них основной, неясно. */
  hasOtherCities: boolean;
  /** Город сайта известен, а счётчика для него на сайте нет. */
  cityCounterMissing: boolean;
  /** Основным сейчас стоит счётчик какого-то города. */
  primaryIsCity: boolean;
  /** Что сказать владельцу; пустая строка — говорить нечего. */
  text: string;
};

const MAIN_CITY_TEXT = "На сайте есть отдельный счётчик для вашего города.";
const OTHER_CITY_TEXT =
  "Сейчас визиты считаются по счётчику другого города. Если реклама идёт по вашему городу, точнее считать по городскому.";
const ALL_CITIES_TEXT =
  "Сейчас визиты считаются по общему — в него попадают все города. Если реклама идёт по одному городу, точнее считать по городскому.";
const UNKNOWN_CITY_TEXT =
  "На сайте есть отдельные счётчики для городов, но какой из них ваш, сайт не сообщает. Если визиты и заявки считаются неточно, укажите номер счётчика вашего города вручную.";
const MISSING_CITY_TEXT =
  "Для вашего города счётчик на сайте не нашёлся. Если визиты и заявки считаются неточно, укажите его номер вручную.";

export function cityAdvice(
  cityCounters: Record<string, number>,
  defaultCityId: string | null,
  counters: number[],
): CityAdvice {
  const entries = Object.entries(cityCounters);
  const defaultKey =
    defaultCityId === null
      ? null
      : entries.find(([cityId]) => Number(cityId) === Number(defaultCityId))?.[0] ?? null;
  const mainCounter = defaultKey === null ? null : cityCounters[defaultKey] ?? null;
  const primary = counters[0] ?? null;
  const primaryIsCity = primary !== null && Object.values(cityCounters).includes(primary);
  const canUseMainCity = mainCounter !== null && primary !== mainCounter;
  const cityCounterMissing = defaultCityId !== null && mainCounter === null;
  const hasOtherCities = entries.length > 0 && mainCounter === null;

  let text = "";
  if (canUseMainCity) {
    if (counters.length === 0) {
      text = `${MAIN_CITY_TEXT} Добавьте его — по нему будем считать визиты и заявки.`;
    } else if (primaryIsCity) {
      text = `${MAIN_CITY_TEXT} ${OTHER_CITY_TEXT}`;
    } else {
      text = `${MAIN_CITY_TEXT} ${ALL_CITIES_TEXT}`;
    }
  } else if (hasOtherCities) {
    text = cityCounterMissing ? MISSING_CITY_TEXT : UNKNOWN_CITY_TEXT;
  }
  return { mainCounter, canUseMainCity, hasOtherCities, cityCounterMissing, primaryIsCity, text };
}

/** Название для счётчика города: набранное владельцем важнее сохранённого.
 *
 * В черновике ключ есть, если владелец печатал в поле или у счётчика было
 * сохранённое название. Пустое значение считаем осознанно очищенным полем.
 */
export function mainCityName(
  drafts: Record<string, string>,
  saved: Record<string, string>,
  counterId: number,
): string | undefined {
  const draft = drafts[String(counterId)];
  if (draft !== undefined) {
    return draft.trim() || undefined;
  }
  return (saved[String(counterId)] ?? "").trim() || MAIN_CITY_NAME;
}

/** Ставит счётчик города первым и основным, остальные оставляет резервными с их названиями. */
export function withMainCityFirst(
  items: CounterItem[],
  counterId: number,
  name: string | undefined,
): CounterItem[] {
  const rest = items.filter((item) => item.id !== counterId);
  return [
    { id: counterId, ...(name ? { name } : {}), primary: true },
    ...rest.map((item) => ({ ...item, primary: false })),
  ];
}
