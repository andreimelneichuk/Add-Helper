import { rublesToMicros } from "./money";

// Тестовые данные прототипа. Настоящие придут из API после подключения Директа.
export const HOME = {
  periodLabel: "ноябрь",
  budget: rublesToMicros(30_000),
  spent: rublesToMicros(11_400),
  forecastSpent: rublesToMicros(27_600),
  balance: rublesToMicros(9_200),
  requests: 28,
  confirmedClients: 9,
  updatedAt: "сегодня в 14:20",
};

export type RollbackKind = "plain" | "has_later" | "impossible";

export interface HistoryEntry {
  id: number;
  title: string;
  why: string;
  next: string;
  when: string;
  rollback: RollbackKind;
  consequence: string;
  reverted?: boolean;
  isRollback?: boolean;
}

export const HISTORY: HistoryEntry[] = [
  {
    id: 3,
    title: "Лимит направления «Маникюр» поднят до 12 000 ₽",
    why: "За 14 дней цена обращения 310 ₽ при вашей цели 400 ₽. Общий бюджет не изменился: 2 000 ₽ перенесли с «Педикюра».",
    next: "Проверим результат через неделю.",
    when: "вчера",
    rollback: "plain",
    consequence: "Лимит вернётся к 10 000 ₽, а общий бюджет останется в рамках 30 000 ₽. Уже потраченные деньги не вернутся.",
  },
  {
    id: 2,
    title: "Лимит направления «Педикюр» снижен до 6 000 ₽",
    why: "За 14 дней потрачено 3 100 ₽, обращений 3, цена обращения 1 033 ₽ при вашей цели 400 ₽.",
    next: "Освободившиеся деньги перенесли на «Маникюр».",
    when: "вчера",
    rollback: "has_later",
    consequence: "Сначала отменится более позднее изменение: подъём лимита «Маникюра». Иначе сумма лимитов превысила бы ваш бюджет.",
  },
  {
    id: 1,
    title: "Отключено объявление «Маникюр с покрытием за 1 500 ₽»",
    why: "За 12 дней потрачено 1 840 ₽, обращений нет, при вашей цене обращения 400 ₽. Другие объявления за то же время принесли 5 обращений.",
    next: "Его доля показов перешла к другим объявлениям.",
    when: "3 дня назад",
    rollback: "plain",
    consequence: "Объявление снова включится. Бюджет не вырастет: оно разделит его с другими.",
  },
  {
    id: 0,
    title: "Реклама остановлена: расход шёл слишком быстро",
    why: "За 2 часа потрачено 2 900 ₽, обычно за это время уходит около 300 ₽. Остановили, чтобы не выйти за бюджет.",
    next: "Проблема решена, реклама снова работает.",
    when: "неделю назад",
    rollback: "impossible",
    consequence: "Эту остановку нельзя отменить кнопкой: она защищала ваш бюджет, и её снимают только после проверки расхода.",
  },
];

/** Отмена не стирает запись: появляется новая, а исходная помечается отменённой. */
export function applyRollback(entries: HistoryEntry[], id: number): HistoryEntry[] {
  const target = entries.find((entry) => entry.id === id);
  if (!target || target.rollback === "impossible" || target.reverted) return entries;
  const toRevert = entries.filter(
    (entry) => entry.id === id || (target.rollback === "has_later" && entry.id > id && !entry.isRollback && !entry.reverted),
  );
  const revertedIds = new Set(toRevert.map((entry) => entry.id));
  const nextId = Math.max(...entries.map((entry) => entry.id)) + 1;
  const records: HistoryEntry[] = toRevert.map((entry, offset) => ({
    id: nextId + offset,
    title: `Отменено: ${entry.title}`,
    why: "Вы отменили это изменение и вернули прежнее значение. Потраченные деньги и данные обучения вернуть нельзя.",
    next: "Система не будет повторять это действие сама.",
    when: "только что",
    rollback: "impossible",
    consequence: "Это уже отмена.",
    isRollback: true,
  }));
  return [
    ...records.reverse(),
    ...entries.map((entry) => (revertedIds.has(entry.id) ? { ...entry, reverted: true } : entry)),
  ];
}
