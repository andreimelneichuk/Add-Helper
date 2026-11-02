// Подставные данные прототипа и будущий контракт API (этап R7). Деньги в целых рублях.

export type Situation = "running" | "first" | "problem" | "verdict" | "budget" | "rejected" | "access";
export type View = "overview" | "stats" | "ads" | "history" | "settings";
export type Period = "week" | "month";
export type SettingKey = "services" | "area" | "hours" | "goal" | "budget";
export type HistoryKind = "money" | "ads" | "tests" | "yours";
/** Расход за день в рублях и число обращений. */
export type Day = [number, number];
export type HistoryEntry = { id: string; kind: HistoryKind; when: string; title: string; why: string; next: string; canUndo: boolean; undoNote?: string; state?: string; revert?: { setting?: [SettingKey, string | number]; ad?: string; adOff?: string; adContent?: Ad }; about?: string };
export type TagTone = 'good' | 'accent' | 'neutral';
export type Ad = { id: string; name: string; tag: string; tagTone: TagTone; leads: number; cost: number; hook: string; title: string; text: string; facts: string; photo?: string };
/** Название, обращения, расход, объявления; у настоящих данных ещё «кабинет/номер направления». */
type Direction = [string, number, number, [string, number, number, string?][], string?];
export type PeriodStats = { label: string; cost: number; leads: number; shows: number; clicks: number; dirs: Direction[] };
/** quote — дословная строка с сайта, на которой держится факт; у правок владельца её нет. */
export type Fact = { id: string; label: string; value: string; source: string; quote?: string };
export type LiveAlert = { kind: string; title: string; text: string; tone: "bad" | "warn" };
/** Настоящие данные сервиса; суммы в рублях только для показа. */
export type Live = { name: string; today: string; monthName: string; monthOf: string; monthDays: number; days: Day[]; trail: Day[]; spent: number | null; budget: number; balance: number | null; gap: number | null; account: string | null; hasCampaigns: boolean; alerts: LiveAlert[]; stats: Record<Period, PeriodStats>; profile: [string, string][] };
export type Step = { label: string; title: string; lead: string; cta: string; ai: string };

/** Неразрывные пробелы: сумма не должна переноситься по частям. */
export const rub = (n: number): string => Math.round(n).toLocaleString("ru-RU").replace(/\s/g, " ") + " ₽";

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
export const STEPS: Step[] = [
  { label: 'О бизнесе', title: 'Где о вас можно прочитать?', lead: 'Дайте ссылку, остальное я подготовлю сам.', cta: 'Изучить', ai: 'Прочитал сайт и собрал профиль. Проверьте факты на вкладке «Настройка»: в рекламу попадёт только то, что вы подтвердите. Поправить можно и здесь, словами.' },
  { label: 'Профиль', title: 'Вот что я понял о вас', lead: 'Если что-то не так, исправьте здесь. В рекламу попадёт только то, что вы подтвердите.', cta: 'Всё верно', ai: 'Сколько готовы тратить в месяц? Для салона в вашем районе я бы начал с 20 000 ₽. Больше выбранной суммы не потрачу.' },
  { label: 'Бюджет', title: 'Сколько готовы тратить в месяц?', lead: 'Сумму можно поменять в любой момент.', cta: 'Продолжить', ai: 'Подготовил три объявления. Я буду сам улучшать их после запуска, а пока посмотрите их на вкладке «Настройка» и уберите лишние.' },
  { label: 'Реклама', title: 'Вот ваши объявления', lead: 'Изображения и тексты подготовил ИИ, факты только из вашего профиля.', cta: 'Нравится', ai: 'Осталось три шага: кабинет, ИНН и код статистики. Начнём с кабинета.' },
  { label: 'Кабинет', title: 'Подключите кабинет Яндекса', lead: 'Без него нечего запускать. Деньги списываются с вашего счёта в Яндексе, мы их не держим.', cta: 'Продолжить', ai: 'Теперь маркировка: нужен ИНН.' },
  { label: 'Маркировка', title: 'Укажите ИНН для маркировки', lead: 'Этого требует закон о рекламе. Передаём только Яндексу, храним зашифрованным.', cta: 'Продолжить', ai: 'Остался код статистики: без него я не увижу обращения.' },
  { label: 'Статистика', title: 'Поставьте код статистики на сайт', lead: 'По нему я узнаю, кто оставил заявку или позвонил. Если сайтом занимается разработчик, перешлите ему письмо с кодом.', cta: 'Продолжить', ai: 'Всё готово. Проверьте сводку и запускайте.' },
  { label: 'Запуск', title: 'Всё готово к запуску', lead: 'Яндекс проверит объявления, обычно до суток. Я напишу, когда начнутся показы.', cta: 'Запустить рекламу', ai: '' },
];
export const histFor = (id: Situation): HistoryEntry[] => (id === 'verdict' ? [VERDICT_ENTRY, ...HISTORY] : id === 'rejected' ? [REJECTED_ENTRY, ...HISTORY] : id === 'first' ? [FIRST_ENTRY] : HISTORY);

/** Наименьшие суммы для своего значения; на этапе R7 их задают правила и площадка. */
export const MIN_BUDGET = 1000;
export const MIN_GOAL = 100;

export const FACTS: Fact[] = [
  { id: "what", label: "Чем занимаетесь", value: "Маникюр и педикюр", source: "с сайта" },
  { id: "prices", label: "Цены", value: "Маникюр от 1 200 ₽, педикюр от 1 800 ₽", source: "с сайта" },
  { id: "where", label: "Где", value: "Москва, у метро Сокольники", source: "с сайта" },
  { id: "hours", label: "Когда работаете", value: "Каждый день, 10:00–21:00", source: "с сайта" },
  { id: "promo", label: "Акция", value: "Скидка 20% на первый визит", source: "с сайта" },
];

export function leadsWord(n: number) {
  const tens = n % 100;
  const ones = n % 10;
  if (tens >= 11 && tens <= 14) return "обращений";
  if (ones === 1) return "обращение";
  return ones >= 2 && ones <= 4 ? "обращения" : "обращений";
}
