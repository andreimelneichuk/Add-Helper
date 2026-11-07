// Подставные данные прототипа и будущий контракт API (этап R7). Деньги в целых рублях.
import type { Profile, TodayCard } from "../api";

export type Situation = "running" | "first" | "problem" | "verdict" | "budget" | "rejected" | "access";
export type View = "overview" | "stats" | "ads" | "history" | "settings";
export type Period = "week" | "month";
export type SettingKey = "services" | "area" | "hours" | "goal" | "budget";
export type HistoryKind = "money" | "ads" | "tests" | "yours";
/** Расход за день в рублях и число обращений. */
export type Day = [number, number];
export type HistoryEntry = { id: string; kind: HistoryKind; when: string; title: string; why: string; next: string; canUndo: boolean; undoNote?: string; state?: string; revert?: { setting?: [SettingKey, string | number]; ad?: string; adOff?: string; adContent?: Ad }; about?: string };
export type TagTone = 'good' | 'accent' | 'neutral' | 'bad';
export type Ad = { id: string; name: string; tag: string; tagTone: TagTone; leads: number; cost: number; hook: string; title: string; text: string; facts: string; photo?: string };
/** Название, обращения, расход, объявления; у настоящих данных ещё «кабинет/номер направления». */
type Direction = [string, number, number, [string, number, number, string?][], string?];
export type PeriodStats = { label: string; cost: number; leads: number; shows: number; clicks: number; dirs: Direction[] };
/** quote — дословная строка с сайта, на которой держится факт; у правок владельца её нет. */
/** field — поле профиля, куда факт ляжет после входа; у фактов, добавленных словами, его нет. */
export type Fact = { id: string; label: string; value: string; source: string; quote?: string; field?: "niche" | "address" | "service" | "feature" };
export type LiveAlert = { kind: string; title: string; text: string; tone: "bad" | "warn" };
/** Настоящие данные сервиса; суммы в рублях только для показа. */
export type Live = { cards?: TodayCard[]; name: string; today: string; monthName: string; monthOf: string; monthDays: number; days: Day[]; trail: Day[]; spent: number | null; budget: number; balance: number | null; gap: number | null; account: string | null; hasCampaigns: boolean; alerts: LiveAlert[]; stats: Record<Period, PeriodStats>; profile: [string, string][]; rawProfile?: Profile | null; planBudget?: number | null };
export type Step = { label: string; title: string; lead: string; cta: string; ai: string };

/** Неразрывные пробелы: сумма не должна переноситься по частям. */
export const rub = (n: number): string => Math.round(n).toLocaleString("ru-RU").replace(/\s/g, " ") + " ₽";

export const STEPS: Step[] = [
  { label: 'О бизнесе', title: 'Где о вас можно прочитать?', lead: 'Дайте ссылку, остальное я подготовлю сам.', cta: 'Изучить', ai: 'Прочитал сайт и собрал профиль. Проверьте факты на вкладке «Настройка»: в рекламу попадёт только то, что вы подтвердите. Поправить можно и здесь, словами.' },
  { label: 'Профиль', title: 'Вот что я понял о вас', lead: 'Если что-то не так, исправьте здесь. В рекламу попадёт только то, что вы подтвердите.', cta: 'Всё верно', ai: 'Сколько готовы тратить в месяц? Больше выбранной суммы не потрачу.' },
  { label: 'Бюджет', title: 'Сколько готовы тратить в месяц?', lead: 'Сумму можно поменять в любой момент.', cta: 'Продолжить', ai: 'Подготовил три объявления. Я буду сам улучшать их после запуска, а пока посмотрите их на вкладке «Настройка» и уберите лишние.' },
  { label: 'Реклама', title: 'Вот ваши объявления', lead: 'Изображения и тексты подготовил ИИ, факты только из вашего профиля.', cta: 'Нравится', ai: 'Осталось четыре шага: кабинет, маркировка, статистика и запуск. Начнём с кабинета.' },
  { label: 'Кабинет', title: 'Подключите кабинет Яндекса', lead: 'Без него нечего запускать. Деньги списываются с вашего счёта в Яндексе, мы их не держим.', cta: 'Продолжить', ai: 'Теперь маркировка: нужен ИНН.' },
  { label: 'Маркировка', title: 'Укажите ИНН для маркировки', lead: 'Этого требует закон о рекламе. Передаём только Яндексу, храним зашифрованным.', cta: 'Продолжить', ai: 'Остался код статистики: без него я не увижу обращения.' },
  { label: 'Статистика', title: 'Поставьте код статистики на сайт', lead: 'По нему я узнаю, кто оставил заявку или позвонил. Если сайтом занимается разработчик, перешлите ему письмо с кодом.', cta: 'Продолжить', ai: 'Всё готово. Проверьте сводку и запускайте.' },
  { label: 'Запуск', title: 'Всё готово к запуску', lead: 'Яндекс проверит объявления, обычно до суток. Я напишу, когда начнутся показы.', cta: 'Запустить рекламу', ai: '' },
];

/** Наименьшие суммы для своего значения; на этапе R7 их задают правила и площадка. */
export const MIN_BUDGET = 1000;
export const MIN_GOAL = 100;


export function leadsWord(n: number) {
  const tens = n % 100;
  const ones = n % 10;
  if (tens >= 11 && tens <= 14) return "обращений";
  if (ones === 1) return "обращение";
  return ones >= 2 && ones <= 4 ? "обращения" : "обращений";
}
