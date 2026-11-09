from dataclasses import dataclass


@dataclass(frozen=True)
class AutopilotRules:
    version: str
    maturity_days: int
    learning_days: int
    learning_target_multiple: int
    prior_weight: int
    good_probability_bp: int
    bad_probability_bp: int
    bad_cpl_multiple_bp: int
    bad_min_spend_multiple: int
    min_change_gap_days: int
    budget_increase_bp: int
    health_recent_days: int
    health_min_clicks: int
    health_min_visit_share_bp: int
    health_min_expected_leads: int
    health_gap_share_bp: int
    # A/B-тест по итогам анализа, docs/04-autopilot.md §5. Нули сохраняют решения autopilot-1:
    # тогда пограничное объявление сразу получает тест, без проверки бюджета и отказов.
    test_days: int = 7
    test_min_target_multiple: int = 0
    test_decline_hold_days: int = 0
    # Оценка по ожидаемой потере (autopilot-3, docs/16-autopilot-autonomy.md §11). Движок
    # «classic» — прежние правила по цене обращения, нули ничего не включают.
    engine: str = "classic"
    # Сила априора группы в «воображаемых кликах»: малые данные тянутся к среднему направления.
    prior_clicks: int = 0
    # Хвост интервала в базисных пунктах с каждой стороны: 500 — интервал 90%.
    interval_tail_bp: int = 500
    # Меньше кликов объявление не сравниваем, какими бы плохими ни были цифры.
    min_clicks: int = 0
    # Вероятность, что вариант действительно хуже, ниже которой не действуем.
    min_worse_probability_bp: int = 0
    # Цена ошибки (потерять хороший вариант, сбить обучение) в целевых ценах обращения.
    error_cost_target_multiple: int = 0
    # Для объявления моложе learning_days цена ошибки умножается на это число.
    learning_error_multiple: int = 1
    # Меньше обращений в неделю в направлении: оптимизацию не ведём, только защита.
    min_weekly_leads: int = 0
    # Больше вариантов в одном направлении не сравниваем.
    max_variants: int = 2
