// Подставные данные прототипа (`/?mock` в сборке для разработки) и тестов. В боевую сборку не входят.
import { rub } from "../app/data";
import type { Ad, Day, Fact, HistoryEntry, Period, PeriodStats, SettingKey, Situation } from "../app/data";
import type { Fixtures } from "../app/fixtures";
import type { State } from "../app/store";

const BASE: Day[] = [[420, 1], [480, 0], [510, 1], [450, 1], [490, 1], [520, 1], [490, 1], [640, 1], [720, 2], [680, 1], [810, 2], [640, 1], [790, 3], [760, 2]];
const LATER: Day[] = [800, 830, 860, 820, 840, 850, 870, 830, 860, 840, 850, 800, 850].map((c): Day => [c, 2]);
export const DAYS: Record<Situation, Day[]> = { running: BASE, first: [[300, 0], [340, 1]], problem: BASE, verdict: [...BASE, LATER[0]!], budget: [...BASE, ...LATER], rejected: BASE, access: BASE };
export const ACCOUNT: Record<Situation, number> = { running: 7200, first: 14360, problem: 0, verdict: 6400, budget: 900, rejected: 7200, access: 7200 };
export const OPENING: Record<Situation, string> = {
  first: 'Реклама идёт второй день. Первые две недели я собираю данные и ничего резко не меняю. Первое обращение уже есть, первые выводы примерно через 12 дней.',
  rejected: 'Яндекс отклонил «Маникюр от 1 200 ₽»: в тексте не хватило адреса. Я добавил адрес и отправил на повторную проверку. От вас ничего не нужно.',
  access: 'Пропал доступ к кабинету Яндекса. Я ничего не могу менять, но лимиты стоят в самом кабинете, поэтому перерасхода не будет. Нужно подключить кабинет заново.',
  running: 'За 7 дней 12 обращений по 420 ₽, неделей раньше было 6 по 560 ₽. Вмешиваться не нужно. Один вопрос: сколько обращений стали клиентами? Можно ответить здесь или в обзоре.',
  problem: 'Показы остановились в 14:20: на счёте в Яндексе 0 ₽. Ваш бюджет не изменился, до конца месяца можно потратить ещё 11 600 ₽. Пополните кабинет, и я включу показы сам.',
  verdict: 'Проверка закончена: скидка работает лучше цены, 11 обращений по 330 ₽ против 4 по 880 ₽. Оставил скидку. Уверенность высокая, но решение можно отменить.',
  budget: 'До конца месяца 3 дня, а в бюджете осталось 700 ₽. Если ничего не менять, показы остановятся примерно 29 ноября и включатся 1 декабря. Перерасхода не будет.',
};
const HISTORY: HistoryEntry[] = [
  { id: 'off', kind: 'ads', when: '13 нояб', title: 'Отключил «Маникюр с покрытием за 1 500 ₽»', why: 'За 12 дней 1 840 ₽ и ни одного обращения, другие за это время принесли 5.', next: 'Его деньги получил «Первый визит со скидкой».', canUndo: true, undoNote: 'Объявление снова включится и разделит бюджет с другими. Общий бюджет не вырастет.' },
  { id: 'move', kind: 'money', when: '10 нояб', title: 'Передал 3 000 ₽ объявлению «Первый визит со скидкой»', why: 'Обращения по нему стоят 329 ₽ при вашей цели 400 ₽.', next: 'Общий бюджет тот же.', canUndo: true, undoNote: 'Сначала снижу лимит этого объявления, потом подниму другим. Общий бюджет не изменится.' },
  { id: 'photo', kind: 'tests', when: '9 нояб', title: 'Проверка закончена: фото работ лучше фото салона', why: '11 обращений против 4 за 14 дней.', next: 'Оставил фото работ.', canUndo: false, state: 'Можно проверить заново' },
  { id: 'start', kind: 'yours', when: '1 окт', title: 'Запустил рекламу', why: 'Вы подтвердили бюджет и объявления.', next: 'Яндекс проверил объявления за 6 часов.', canUndo: false, state: 'Остановка кнопкой «Пауза»' },
];
const REJECTED_ENTRY: HistoryEntry = { id: 'rejected', kind: 'ads', when: '14 нояб', title: 'Исправил «Маникюр от 1 200 ₽» после отказа Яндекса', why: 'Яндекс отклонил объявление: в тексте не хватило адреса.', next: 'Добавил адрес из профиля, жду повторной проверки.', canUndo: false, state: 'На проверке' };
const FIRST_ENTRY: HistoryEntry = { id: 'start', kind: 'yours', when: '31 окт', title: 'Запустил рекламу', why: 'Вы подтвердили бюджет и объявления.', next: 'Собираю данные около двух недель.', canUndo: false, state: 'Остановка кнопкой «Пауза»' };
const VERDICT_ENTRY: HistoryEntry = { id: 'verdict', kind: 'tests', when: '15 нояб', title: 'Отключил вариант «Маникюр от 1 200 ₽»', why: '4 обращения по 880 ₽ против 11 по 330 ₽ за 14 дней.', next: 'Его долю получил победитель.', canUndo: true, undoNote: 'Оба варианта снова поделят бюджет поровну. Общий бюджет не вырастет.' };
export const ADS: Ad[] = [
  { id: 'a1', name: 'Первый визит со скидкой', tag: 'лучшее', tagTone: 'good', leads: 7, cost: 2300, hook: 'Минус 20% на первый маникюр', title: 'Маникюр у метро Сокольники, скидка 20%', text: 'Скидка на первый визит. Запись на сегодня, работаем до 21:00.', facts: 'Скидка из профиля, часы с сайта' },
  { id: 'a2', name: 'Маникюр от 1 200 ₽', tag: 'проверяю', tagTone: 'accent', leads: 2, cost: 1180, hook: 'Маникюр от 1 200 ₽', title: 'Маникюр от 1 200 ₽', text: 'У метро Сокольники. Открыты каждый день до 21:00.', facts: 'Цена и часы с сайта' },
  { id: 'a3', name: 'Педикюр рядом с домом', tag: 'работает', tagTone: 'neutral', leads: 3, cost: 1560, hook: 'Педикюр у дома, от 1 800 ₽', title: 'Педикюр рядом с домом', text: 'Педикюр от 1 800 ₽ у метро Сокольники. Запишитесь онлайн.', facts: 'Цена и адрес с сайта' },
];
export const STATS: Record<Period, PeriodStats> = {
  week: { label: '7 дней', cost: 5040, leads: 12, shows: 8420, clicks: 312, dirs: [['Маникюр', 9, 3480, [['Первый визит со скидкой', 7, 2300], ['Маникюр от 1 200 ₽', 2, 1180]]], ['Педикюр', 3, 1560, [['Педикюр рядом с домом', 3, 1560]]]] },
  month: { label: 'С 1 ноября', cost: 8400, leads: 18, shows: 14100, clicks: 530, dirs: [['Маникюр', 13, 5900, [['Первый визит со скидкой', 10, 4000], ['Маникюр от 1 200 ₽', 3, 1900]]], ['Педикюр', 5, 2500, [['Педикюр рядом с домом', 5, 2500]]]] },
};
export const OPTIONS: Record<SettingKey, { label: string; values: (string | number)[] }> = {
  services: { label: 'Что рекламируем', values: ['маникюр и педикюр', 'только маникюр', 'маникюр, педикюр и брови'] },
  area: { label: 'Где', values: ['Сокольники', 'Сокольники и Преображенка', 'вся Москва'] },
  hours: { label: 'Когда', values: ['в часы работы', 'кроме воскресений', 'круглосуточно'] },
  goal: { label: 'Желаемая цена обращения', values: [300, 400, 500] },
  budget: { label: 'Бюджет в месяц', values: [15000, 20000, 25000] },
};
const histFor = (id: Situation): HistoryEntry[] => (id === 'verdict' ? [VERDICT_ENTRY, ...HISTORY] : id === 'rejected' ? [REJECTED_ENTRY, ...HISTORY] : id === 'first' ? [FIRST_ENTRY] : HISTORY);
export const FACTS: Fact[] = [
  { id: "what", field: "niche", label: "Чем занимаетесь", value: "Маникюр и педикюр", source: "с сайта" },
  { id: "prices", field: "service", label: "Цены", value: "Маникюр от 1 200 ₽, педикюр от 1 800 ₽", source: "с сайта" },
  { id: "where", field: "address", label: "Где", value: "Москва, у метро Сокольники", source: "с сайта" },
  { id: "hours", field: "feature", label: "Когда работаете", value: "Каждый день, 10:00–21:00", source: "с сайта" },
  { id: "promo", field: "feature", label: "Акция", value: "Скидка 20% на первый визит", source: "с сайта" },
];

type Template = { needs: string[]; hook: (s: State) => string; title: (s: State) => string; text: string; facts: string };
type Topic = "manicure" | "pedicure" | "promo";
const manicurePrice = (s: State) => rub(s.price ?? 1200);
// Заглушка на месте языковой модели: варианты собраны только из фактов профиля, needs говорит, из каких.
const TEMPLATES: Record<Topic, Template[]> = {
  manicure: [
    { needs: ["where", "hours"], hook: () => "Маникюр у метро Сокольники", title: () => "Маникюр рядом с домом", text: "Открыты каждый день до 21:00. Запишитесь онлайн.", facts: "Адрес и часы из профиля" },
    { needs: ["prices", "where"], hook: (s) => `Маникюр от ${manicurePrice(s)}`, title: (s) => `Маникюр от ${manicurePrice(s)} у метро Сокольники`, text: "Запишитесь онлайн на удобное время.", facts: "Цена и адрес из профиля" },
    { needs: ["what"], hook: () => "Аккуратный маникюр", title: () => "Запишитесь на маникюр", text: "Свободное время видно сразу при записи.", facts: "Услуга из профиля" },
  ],
  pedicure: [
    { needs: ["where", "hours"], hook: () => "Педикюр у метро Сокольники", title: () => "Педикюр рядом с домом", text: "Открыты каждый день до 21:00. Запишитесь онлайн.", facts: "Адрес и часы из профиля" },
    { needs: ["prices", "where"], hook: () => "Педикюр от 1\u00a0800\u00a0₽", title: () => "Педикюр от 1\u00a0800\u00a0₽ у метро Сокольники", text: "Запишитесь онлайн на удобное время.", facts: "Цена и адрес из профиля" },
    { needs: ["what"], hook: () => "Педикюр без спешки", title: () => "Запишитесь на педикюр", text: "Свободное время видно сразу при записи.", facts: "Услуга из профиля" },
  ],
  promo: [
    { needs: ["promo", "where"], hook: () => "Минус 20% на первый визит", title: () => "Скидка 20% у метро Сокольники", text: "Для тех, кто приходит к нам впервые. Запишитесь онлайн.", facts: "Скидка и адрес из профиля" },
    { needs: ["promo", "hours"], hook: () => "Первый визит на 20% дешевле", title: () => "Скидка 20% на первый визит", text: "Открыты каждый день до 21:00.", facts: "Скидка и часы из профиля" },
  ],
};
const amounts = (text: string) => [...text.matchAll(/(\d[\d\s]*?)\s*(%|₽|руб|процент)/gi), ...text.matchAll(/скидк\S*\s+(\d+)()/gi)].map((m) => ({ num: m[1]!.replace(/\s/g, ""), shown: m[0].trim() }));
/** Чего из просьбы нельзя писать: реклама говорит только то, что подтверждено профилем. */
function askRefusal(s: State, ask: string): string | null {
  const known = new Set(amounts([...s.facts.map((f) => f.value), s.price ? rub(s.price) : ""].join(" ")).map((a) => a.num));
  const unknown = amounts(ask).find((a) => !known.has(a.num));
  if (unknown) return `В профиле нет «${unknown.shown}». Добавьте это в профиль, и я смогу написать об этом в рекламе.`;
  const hasPromo = s.facts.some((f) => f.id === "promo" || /скидк|акци/i.test(f.value) || /скидк|акци/i.test(f.label));
  if (/скидк|акци/i.test(ask) && !hasPromo) return "В профиле нет акции. Добавьте её в профиль, и я смогу написать о ней в рекламе.";
  if (/отзыв/i.test(ask)) return "Отзыв можно использовать только с подтверждённым согласием клиента. Пока его нет, напишу без отзыва.";
  if (/лучш|№\s*1|гарантир/i.test(ask)) return "Такое обещание нечем подтвердить, а закон о рекламе требует подтверждения. Напишу о том, что есть в профиле.";
  return null;
}
const topicOf = (text: string): Topic | null => (/педикюр/i.test(text) ? "pedicure" : /маникюр/i.test(text) ? "manicure" : /акци|скидк/i.test(text) ? "promo" : null);

/** Новый вариант по просьбе; base null у нового объявления. В показы он сам не идёт. */
let nextDraft = 1;
function draftAd(s: State, base: Ad | null, ask: string, attempt = 0): { ad: Ad } | { refusal: string } {
  const refusal = askRefusal(s, ask);
  if (refusal) return { refusal };
  if (base && /короче/i.test(ask)) return { ad: { ...base, text: base.text.split(/(?<=\.)\s/)[0]! } };
  const topic = topicOf(ask) ?? (base ? topicOf(`${base.name} ${base.title}`) : null) ?? "manicure";
  const have = new Set(s.facts.map((f) => f.id));
  if (s.facts.some((f) => f.id === "promo" || /скидк|акци/i.test(f.value) || /скидк|акци/i.test(f.label))) {
    have.add("promo");
  }
  const fit = TEMPLATES[topic].filter((t) => t.needs.every((n) => have.has(n)) && t.hook(s) !== base?.hook);
  const t = fit[attempt % Math.max(1, fit.length)];
  if (!t) return { refusal: "В профиле пока мало фактов для такого объявления. Добавьте услуги, цены или адрес." };
  const content = { hook: t.hook(s), title: t.title(s), text: t.text, facts: t.facts };
  return { ad: base ? { ...base, ...content } : { id: "n" + nextDraft++, name: content.hook, tag: "новое", tagTone: "accent", leads: 0, cost: 0, ...content } };
}
const TEXT: Fixtures["TEXT"] = {
  area: "Сокольники",
  audience: "Жителям Сокольников",
  rewind: "Прототип: перематываю на 14 ноября, как будто прошло две недели.",
  rejected: "В «Маникюр от 1 200 ₽» не хватало адреса. Добавил его из профиля и жду повторной проверки. От вас ничего не нужно.",
  profile: (price) => [
    ["Цены", `маникюр от ${rub(price ?? 1200)}, педикюр от 1 800 ₽`],
    ["Адрес", "Москва, у метро Сокольники"],
    ["Часы", "каждый день, 10:00–21:00"],
    ["Акция", "скидка 20% на первый визит"],
  ],
};

export const mockFixtures: Fixtures = { TEXT, DAYS, ACCOUNT, OPENING, ADS, STATS, OPTIONS, FACTS, histFor, draftAd };
