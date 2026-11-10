"""Ответ владельца на предложение теста и отмена готовящегося теста.

Показы здесь не меняются: тест готовится без варианта, переключения начнутся после модерации.
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from adhelper.api.cabinet import OwnedChecked, _now, _sessions
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.adapters.tests import SqlTestPlanner
from adhelper.autopilot.application.proposals import Answer, answer_test, owner_cancelled
from adhelper.experiments.adapters.sql import SqlExperimentStore
from adhelper.experiments.application.service import IllegalTransition, cancel_preparing

CURRENT_EXPERIMENT_RULES = "experiments-2"
ANSWERS = {
    Answer.PREPARED: "Готовлю тест. Пока вариант не отправлен, объявление работает как раньше",
    Answer.DECLINED: "Хорошо, теста не будет. Это объявление не предложу две недели",
    Answer.TEST_RUNNING: "На этом направлении уже идёт тест. Новый начнётся после него",
}
REFUSED = {
    Answer.STALE: (status.HTTP_409_CONFLICT, "На это предложение уже ответили"),
    Answer.NOT_FOUND: (status.HTTP_404_NOT_FOUND, "Предложение не найдено"),
}


class OfferAnswer(BaseModel):
    agree: bool


def experiments_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.post("/tests/offers/{decision_id}")
    async def post_offer_answer(
        request: Request, decision_id: int, body: OfferAnswer, business_id: OwnedChecked
    ) -> dict[str, str]:
        sessions = _sessions(request)
        answer = await answer_test(
            SqlDecisionLog(sessions, business_id),
            SqlTestPlanner(sessions, business_id, CURRENT_EXPERIMENT_RULES),
            decision_id,
            body.agree,
            _now(request),
        )
        if answer in REFUSED:
            code, text = REFUSED[answer]
            raise HTTPException(code, text)
        return {"status": answer.value, "text": ANSWERS[answer]}

    @router.post("/tests/{experiment_id}/cancel")
    async def post_cancel(
        request: Request, experiment_id: int, business_id: OwnedChecked
    ) -> dict[str, str]:
        sessions = _sessions(request)
        store = SqlExperimentStore(sessions, business_id)
        try:
            record = await cancel_preparing(store, experiment_id)
        except LookupError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Тест не найден") from None
        except IllegalTransition:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Тест уже идёт: показы переключались, его отмена пока недоступна",
            ) from None
        # Отмена — тот же отказ: объявление не предлагается снова на срок отказа.
        decisions = SqlDecisionLog(sessions, business_id)
        await owner_cancelled(decisions, record.account, record.control_ref, _now(request))
        return {
            "status": "cancelled",
            "text": "Тест отменён. Показы не менялись, это объявление не предложу две недели",
        }

    return router
