// Словарь интерфейса: слова владельца вместо терминов площадки (docs/01-product-spec.md).

export const CAMPAIGN_STATE: Record<string, { label: string; tone: "ok" | "off" | "warn" }> = {
  on: { label: "Идут показы", tone: "ok" },
  suspended: { label: "Остановлено", tone: "off" },
  moderation: { label: "Проверка Яндексом", tone: "warn" },
  archived: { label: "В архиве", tone: "off" },
};

export const SOURCE: Record<string, string> = {
  autopilot: "Автопилот",
  budget_guard: "Защита бюджета",
  user: "Вы",
  rollback: "Отмена",
  experiment: "Проверка вариантов",
  external: "Изменено в кабинете Яндекса",
};

export const CHANGE_KIND: Record<string, string> = {
  set_limit: "Дневной лимит",
  pause: "Остановка показов",
  pause_ad: "Отключено объявление",
  resume_ad: "Включено объявление",
};

export const VERDICT: Record<string, string> = {
  efficient: "Окупается — оставить как есть",
  inefficient: "Не окупается",
  borderline: "На грани — наблюдаем",
  low_data: "Мало данных для вывода",
};

export const CHANGE_STATUS: Record<string, string> = {
  planned: "Запланировано",
  applying: "Применяется",
  applied: "Выполнено",
  failed: "Не удалось",
  rejected: "Отклонено правилами",
  conflict: "Изменено кем-то ещё",
};

export const REJECTION: Record<string, string> = {
  too_long: "Не помещается в лимит длины",
  empty: "Пустой текст",
  unconfirmed_number: "Число не из вашего профиля",
  unconfirmed_claim: "Обещание не из вашего профиля",
  unconfirmed_by_judge: "Утверждение не подтверждено профилем",
  forbidden_word: "Запрещённая формулировка",
  foreign_word: "Иностранное слово",
  too_similar: "Почти повторяет другое объявление",
};

export const SET_NOTE: Record<string, string> = {
  no_profile: "Заполните услуги в профиле: без них объявления писать не из чего.",
  stale: "Генерация прервалась. Попробуйте ещё раз.",
  model_unavailable: "Модель не ответила, взяли шаблоны из профиля.",
  token_limit: "Достигнут лимит расходов на один набор.",
  judge_rejected_all: "Проверка смысла сняла все варианты модели, взяли шаблоны.",
  all_drafts_rejected: "Ни один вариант модели не прошёл проверку.",
  bad_model_reply: "Модель ответила не по форме.",
};

export type AlertText = { title: string; tone: "error" | "warn" };

export const ALERT: Record<string, AlertText> = {
  balance_mismatch: { title: "Счёт не сходится со статистикой", tone: "error" },
  zero_balance: { title: "На счёте закончились деньги", tone: "error" },
  budget_stopped: { title: "Бюджет месяца исчерпан, показы остановлены", tone: "warn" },
  budget_stop_pending: { title: "Не удалось остановить показы", tone: "error" },
  budget_near_limit: { title: "Бюджет месяца почти израсходован", tone: "warn" },
  platform_unavailable: { title: "Яндекс Директ не отвечает", tone: "warn" },
  platform_not_connected: { title: "Кабинет Яндекс Директа не подключён", tone: "warn" },
};
