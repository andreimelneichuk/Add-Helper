"""Сбор фактов для ленты «Сегодня»: окно 7 дней по московскому времени и журнал."""

from collections.abc import Callable
from datetime import datetime

from adhelper.stats.application.overview import overview
from adhelper.stats.domain.model import Dashboard
from adhelper.stats.domain.today import ALERTS, Card, TodayFacts, TodayRules, cards

# Карточка нужна HTTP-слою, который сам домен не импортирует.
__all__ = ["Card", "today"]
WEEK = 7
HISTORY = 30


async def today(
    dashboard: Dashboard,
    now: datetime,
    tolerance_bp: Callable[[str], int],
    rules: TodayRules,
) -> list[Card]:
    view = await overview(dashboard, now, tolerance_bp, days=WEEK)
    running = [line for line, _ in view.campaigns if line.state != "archived"]
    # Пауза владельца: все идущие направления стоят, и остановила их не защита.
    guarded = any(a.kind in ALERTS and ALERTS[a.kind].tone == "bad" for a in view.alerts)
    paused = bool(running) and all(c.state == "suspended" for c in running) and not guarded
    gaps = [m.balance_gap for m in view.money if m.balance_gap is not None]
    facts = TodayFacts(
        now=now,
        alerts=view.alerts,
        balance_gap=max(gaps) if gaps else None,
        paused=paused,
        leads=view.totals.conversions,
        cost=view.totals.cost,
        prev_leads=view.previous.conversions,
        prev_cost=view.previous.cost,
        history=await dashboard.history(HISTORY),
    )
    return cards(facts, rules)
