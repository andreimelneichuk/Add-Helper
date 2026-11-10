from collections.abc import Mapping

UNDO = "Отменить можно в истории одной кнопкой."


def rubles(micros: int) -> str:
    """Округление только при показе."""
    whole = (micros + 500_000) // 1_000_000
    return f"{whole:,}".replace(",", " ") + " ₽"


def percent(bp: int) -> str:
    return f"{bp // 100}%"


def explain(kind: str, facts: Mapping[str, int]) -> str:
    """Числа подставляет код; отсутствующий факт — ошибка, а не выдуманное значение."""
    if kind == "pause_ad":
        return (
            f"Приостановили объявление. За период оно потратило {rubles(facts['spend_micros'])} "
            f"и принесло обращений: {facts['leads']}, а цель — {rubles(facts['target_micros'])} "
            f"за обращение. Вероятность, что цена заметно выше цели: {percent(facts['p_bad_bp'])}. "
            f"Деньги пойдут на другие объявления этой группы. {UNDO}"
        )
    if kind == "increase_limit":
        return (
            f"Увеличили лимит кампании с {rubles(facts['limit_micros'])} до "
            f"{rubles(facts['new_limit_micros'])}. Лучшее объявление принесло обращений: "
            f"{facts['leads']} при расходе {rubles(facts['spend_micros'])}, вероятность "
            f"уложиться в цель {rubles(facts['target_micros'])}: {percent(facts['p_good_bp'])}. "
            f"Следующее увеличение не раньше чем через неделю. {UNDO}"
        )
    if kind == "start_test":
        return (
            f"Объявление потратило {rubles(facts['spend_micros'])} и принесло обращений: "
            f"{facts['leads']}. Данных не хватает для вывода, поэтому нужен A/B-тест."
        )
    raise KeyError(kind)


def explain_rollback(facts: Mapping[str, int]) -> str:
    """Текст автоотката: что вернули, почему, с цифрами и что дальше. Числа подставляет код."""
    return (
        f"Вернули объявление в показ: после его остановки цена обращения выросла на "
        f"{percent(facts['cpl_change_bp'])} (было {rubles(facts['before_cpl_micros'])}, "
        f"стало {rubles(facts['after_cpl_micros'])}), а случайный разброс объясняет не больше "
        f"{percent(facts['noise_bp'])}. Автопилот не остановит это объявление снова, пока вы "
        f"сами не решите иначе. Отменить возврат можно в истории одной кнопкой."
    )


def explain_limit_rollback(facts: Mapping[str, int]) -> str:
    """Откат роста лимита: вернули прежний лимит, потому что цена обращения выросла."""
    return (
        f"Вернули прежний дневной лимит кампании {rubles(facts['old_limit_micros'])} вместо "
        f"{rubles(facts['new_limit_micros'])}: после роста лимита цена обращения выросла на "
        f"{percent(facts['cpl_change_bp'])} (было {rubles(facts['before_cpl_micros'])}, "
        f"стало {rubles(facts['after_cpl_micros'])}), а случайный разброс объясняет не больше "
        f"{percent(facts['noise_bp'])}. Рост денег не окупился, поэтому он отменён. "
        f"Отменить возврат можно в истории одной кнопкой."
    )


def explain_limit_rollback_no_leads(facts: Mapping[str, int]) -> str:
    """Откат роста лимита при нуле обращений: цены «после» нет, говорим о расходе."""
    return (
        f"Вернули прежний дневной лимит кампании {rubles(facts['old_limit_micros'])} вместо "
        f"{rubles(facts['new_limit_micros'])}: после роста лимита обращений 0 при расходе "
        f"{rubles(facts['after_spend_micros'])}, а обычно одно обращение стоило "
        f"{rubles(facts['before_cpl_micros'])}. Отменить возврат можно в истории одной кнопкой."
    )


def explain_rollback_no_leads(facts: Mapping[str, int]) -> str:
    """Откат при нуле обращений: цены обращения «после» нет, поэтому говорим об расходе."""
    return (
        f"Вернули объявление в показ: после его остановки обращений 0 при расходе "
        f"{rubles(facts['after_spend_micros'])}, а обычно одно обращение стоило "
        f"{rubles(facts['before_cpl_micros'])}. Автопилот не остановит это объявление снова, "
        f"пока вы сами не решите иначе. Отменить возврат можно в истории одной кнопкой."
    )
