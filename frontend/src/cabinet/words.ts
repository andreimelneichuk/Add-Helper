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
  create_campaign: "Создана кампания",
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

// Объявления, которые система готовит сама: состояние и почему оно такое.
export const CREATIVE_STATE: Record<string, { label: string; tone: "ok" | "off" | "warn" }> = {
  ready: { label: "Написано", tone: "off" },
  reserve: { label: "В запасе", tone: "off" },
  launch_pending: { label: "Готовим к запуску", tone: "ok" },
  needs_owner: { label: "Ждёт вашего решения", tone: "warn" },
  stopped: { label: "Снято системой", tone: "off" },
  withdrawn: { label: "Снято вами", tone: "off" },
};

export const STATE_REASON: Record<string, string> = {
  written: "Написано по данным вашего профиля.",
  place_free: "В направлении было свободное место.",
  no_place: "Все места в направлении заняты: держим на замену, если какое-то объявление снимут.",
  reserve_full: "Запас на замену уже полон.",
  too_similar: "Почти повторяет объявление, которое уже работает.",
  fact_changed: "В профиле изменилась цена или другой факт, на который оно опиралось.",
  owner: "Вы решили его не запускать.",
  owner_confirmed: "Вы разрешили запуск.",
};

// Что будет дальше: часть объяснения, которую владелец ищет в первую очередь.
export const CREATIVE_NEXT: Record<string, string> = {
  ready: "Поставим в направление, когда освободится место.",
  reserve: "Заменит объявление, которое Яндекс отклонит или которое мы снимем.",
  launch_pending: "Отправим на проверку Яндексу. После одобрения начнутся показы.",
  needs_owner:
    "Без вас не запускаем: ниша требует осторожности или смысл текста не удалось проверить автоматически. Запустите его или откажитесь — тогда напишем другое.",
  stopped: "Показов не будет. Если на его месте нужна замена, она уже пишется.",
  withdrawn: "Показов не будет. На его место напишем другое.",
};

export const WRITE_REASON: Record<string, string> = {
  new_campaign: "для нового направления",
  fact_changed: "взамен устаревшего",
  refill: "на освободившееся место",
};

export const PLACEMENT: Record<string, string> = {
  search: "В поиске Яндекса",
  network: "На сайтах и в приложениях",
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
  ads_no_profile: { title: "Не о чем писать объявления: добавьте услуги в профиль", tone: "warn" },
  ads_need_confirmation: { title: "Новые объявления ждут вашего решения", tone: "warn" },
};
