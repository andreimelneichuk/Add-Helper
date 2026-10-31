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
