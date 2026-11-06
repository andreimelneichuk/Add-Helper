"""План бюджета: мастер запуска, шаг «Бюджет». Сумма до запуска; лимиты в Директе ставит запуск."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, StrictInt

from adhelper.api.accounts import CheckedSession, CurrentSession
from adhelper.api.cabinet import Owned, OwnedChecked, _sessions
from adhelper.api.control import MICROS_PER_RUB
from adhelper.budget.adapters.plan_rules import load_plan_rules
from adhelper.budget.adapters.sql import SqlBudgetPlans, SqlBudgetPortfolio
from adhelper.budget.application.plan import PlanProblem, check_plan, split_budget


class PlanBody(BaseModel):
    # Только целые рубли: копейки в плане владельцу не нужны, а округлять деньги молча нельзя.
    rubles: StrictInt


class PortfolioBody(BaseModel):
    mode: Literal["separate", "shared"]
    # Общая сумма на все сайты, целые рубли. Для раздельного режима не нужна.
    rubles: StrictInt | None = None


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

    @router.get("/me/budget-portfolio")
    async def get_portfolio(request: Request, info: CurrentSession) -> dict[str, Any]:
        portfolio = SqlBudgetPortfolio(_sessions(request), info.user.id)
        saved = await portfolio.get()
        count = len(await portfolio.business_ids())
        mode, micros = saved if saved else ("separate", None)
        return {
            "mode": mode,
            "rubles": None if micros is None else micros // MICROS_PER_RUB,
            "businesses": count,
        }

    @router.put("/me/budget-portfolio")
    async def put_portfolio(
        request: Request, body: PortfolioBody, info: CheckedSession
    ) -> dict[str, Any]:
        portfolio = SqlBudgetPortfolio(_sessions(request), info.user.id)
        if body.mode == "separate":
            await portfolio.save_separate()
            return {"mode": "separate", "rubles": None, "shares": []}
        if body.rubles is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите общий бюджет в рублях в месяц"
            )
        rules = load_plan_rules()
        ids = await portfolio.business_ids()
        if not ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Сначала создайте сайт")
        shares = split_budget(body.rubles, len(ids))
        # Доли отличаются не больше чем на рубль: достаточно проверить меньшую и большую.
        for share in (min(shares), max(shares)):
            problem = check_plan(share, rules)
            if problem is PlanProblem.BELOW_MIN:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Яндекс не примет меньше {_rub(rules.min_monthly_rubles)} в месяц"
                    f" на каждый сайт. На {len(ids)} сайта(ов) укажите не меньше"
                    f" {_rub(rules.min_monthly_rubles * len(ids))}",
                )
            if problem is PlanProblem.ABOVE_MAX:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Больше {_rub(rules.max_monthly_rubles)} в месяц на сайт из мастера не ставим:"
                    " проверьте, нет ли лишнего нуля",
                )
        plans: dict[UUID, int] = {
            business_id: share * MICROS_PER_RUB
            for business_id, share in zip(ids, shares, strict=True)
        }
        saved = await portfolio.save_shared(body.rubles * MICROS_PER_RUB, plans, rules.version)
        if not saved:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Список сайтов изменился. Обновите страницу и повторите"
            )
        return {
            "mode": "shared",
            "rubles": body.rubles,
            "shares": [
                {"business_id": str(business_id), "rubles": share}
                for business_id, share in zip(ids, shares, strict=True)
            ],
        }

    return router
