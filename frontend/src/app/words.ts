// Словарь интерфейса: слова владельца вместо терминов площадки (docs/01-product-spec.md).

export const CAMPAIGN_STATE: Record<string, { label: string; tone: "ok" | "off" | "warn" }> = {
  on: { label: "Идут показы", tone: "ok" },
  suspended: { label: "Остановлено", tone: "off" },
  moderation: { label: "Проверка Яндексом", tone: "warn" },
  archived: { label: "В архиве", tone: "off" },
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
  sent: "Отправлено на проверку Яндексу.",
  accepted: "Яндекс одобрил, идут показы.",
  repairing: "Яндекс отклонил. Переписываем с учётом причины.",
  needs_owner: "Яндекс отклонил, а переписать само не получилось.",
  superseded: "Его заменило одобренное объявление.",
  replaced: "Его заменило новое объявление.",
  moderation_rejected: "Переписано после отказа Яндекса.",
  owner_stopped: "Вы отключили его.",
  owner_resumed: "Вы вернули его, Яндекс проверяет снова.",
  lost_test: "Проиграло проверку: другой вариант за тот же срок приводил заметно больше обращений.",
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
  moderation: "Яндекс проверяет, обычно несколько часов. Прежнее объявление работает до одобрения.",
  running: "Идут показы. Отключить можно на экране объявления.",
  rejected: "Если переписать не получится, мы попросим вас решить.",
};

export const WRITE_REASON: Record<string, string> = {
  new_campaign: "для нового направления",
  fact_changed: "взамен устаревшего",
  refill: "на освободившееся место",
  moderation_rejected: "после отказа Яндекса",
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
  ads_rejected: { title: "Яндекс отклонил объявление, переписать не вышло", tone: "warn" },
};
