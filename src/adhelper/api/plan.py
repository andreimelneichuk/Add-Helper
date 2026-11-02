"""План бюджета: мастер запуска, шаг «Бюджет». Сумма до запуска; лимиты в Директе ставит запуск."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, StrictInt

from adhelper.api.cabinet import Owned, OwnedChecked, _sessions
from adhelper.api.control import MICROS_PER_RUB
from adhelper.budget.adapters.plan_rules import load_plan_rules
from adhelper.budget.adapters.sql import SqlBudgetPlans
from adhelper.budget.application.plan import PlanProblem, check_plan


class PlanBody(BaseModel):
    # Только целые рубли: копейки в плане владельцу не нужны, а округлять деньги молча нельзя.
    rubles: StrictInt


def _rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def plan_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    # Пределы открыты до входа: шаг «Бюджет» идёт раньше входа через Яндекс.
    @router.get("/budget-plan/options")
    async def options() -> dict[str, Any]:
        rules = load_plan_rules()
        return {
            "min": rules.min_monthly_rubles,
            "max": rules.max_monthly_rubles,
            "presets": list(rules.presets),
        }

    @router.get("/businesses/{business_id}/budget-plan")
    async def get_plan(request: Request, business_id: Owned) -> dict[str, int | None]:
        micros = await SqlBudgetPlans(_sessions(request), business_id).get()
        return {"rubles": None if micros is None else micros // MICROS_PER_RUB}

    @router.put("/businesses/{business_id}/budget-plan")
    async def put_plan(
        request: Request, body: PlanBody, business_id: OwnedChecked
    ) -> dict[str, int]:
        rules = load_plan_rules()
        problem = check_plan(body.rubles, rules)
        if problem is PlanProblem.BELOW_MIN:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Яндекс не примет бюджет меньше {_rub(rules.min_monthly_rubles)} в месяц."
                f" Укажите не меньше {_rub(rules.min_monthly_rubles)}",
            )
        if problem is PlanProblem.ABOVE_MAX:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Больше {_rub(rules.max_monthly_rubles)} в месяц из мастера не ставим:"
                " проверьте, нет ли лишнего нуля",
            )
        await SqlBudgetPlans(_sessions(request), business_id).set(
            body.rubles * MICROS_PER_RUB, rules.version
        )
        return {"rubles": body.rubles}

    return router
