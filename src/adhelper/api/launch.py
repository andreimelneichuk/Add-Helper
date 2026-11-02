"""Мастер запуска после входа: маркировка, код статистики, готовность к запуску.

Сами кампании создаёт запуск в T6; здесь только данные и проверки перед ним.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, status

from adhelper.accounts.adapters.sql import SqlMarking, account_states
from adhelper.accounts.application.marking import (
    AdvertiserKind,
    Marking,
    MarkingProblem,
    masked,
    save_marking,
)
from adhelper.accounts.application.service import AccountState
from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.api.control import MICROS_PER_RUB
from adhelper.api.plan import _rub
from adhelper.api.site import URL_PROBLEMS
from adhelper.budget.adapters.sql import SqlBudgetPlans
from adhelper.business.adapters.rules_file import load_site_rules, load_tag_rules
from adhelper.business.adapters.tag_checks import SqlTagChecks, TooManyChecks
from adhelper.business.application.site import check_url
from adhelper.business.application.tag import StoredTagCheck, TagStatus
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.adapters.wizard import SqlWizardAds

MARKING_PROBLEMS = {
    MarkingProblem.INN_FORMAT: "ИНН состоит из 10 или 12 цифр",
    MarkingProblem.INN_CHECKSUM: "В ИНН ошибка в цифрах: проверьте по документам",
    MarkingProblem.INN_LENGTH_FOR_KIND: "У организации ИНН из 10 цифр, у ИП и физического лица"
    " из 12 цифр",
    MarkingProblem.NAME: "Укажите наименование организации или ФИО, как в документах",
    MarkingProblem.PHONE: "Для физического лица нужен телефон: так требует маркировка",
    MarkingProblem.OKVED: "Укажите ОКВЭД основного вида деятельности, например 96.02. Он есть"
    " в выписке из ЕГРЮЛ или ЕГРИП",
}


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


KINDS = {
    AdvertiserKind.LEGAL: "организация",
    AdvertiserKind.IP: "ИП",
    AdvertiserKind.PERSON: "физическое лицо",
}
NO_SITE = "Сайта нет: обращения будут видны по звонкам и заявкам из Яндекса"
LAUNCH_LATER = (
    "Создавать кампании в Яндексе сервис начнёт, когда Яндекс откроет доступ к API."
    " Всё, что вы ввели, сохранено: запуск продолжится с этого места."
)


def _check_view(check: StoredTagCheck | None) -> dict[str, Any] | None:
    if check is None:
        return None
    return {
        "status": check.status.value,
        "url": check.url,
        "counters": list(check.counters),
        "problem": check.problem,
    }


async def _site(request: Request, business_id: Any) -> str:
    found = await SqlProfiles(_sessions(request), business_id).get()
    raw = found[1].strip() if found else ""
    if not raw:
        return ""
    url, problem = check_url(raw, load_site_rules())
    return "" if problem is not None else url


def _counter_item(has_profile: bool, site: str, check: StoredTagCheck | None) -> tuple[bool, str]:
    if not has_profile:
        return False, "Код Метрики проверю, когда будет профиль с адресом сайта"
    if not site:
        return True, NO_SITE
    if check is None or check.url != site:
        return False, "Код Метрики на сайте ещё не проверен"
    if check.status is TagStatus.FOUND:
        numbers = ", ".join(str(n) for n in check.counters)
        return True, f"Код Метрики на сайте есть, счётчик {numbers}"
    if check.status in (TagStatus.QUEUED, TagStatus.RUNNING):
        return False, "Проверяю код Метрики на сайте"
    if check.status is TagStatus.MISSING:
        return False, "Кода Метрики на главной странице сайта нет"
    return False, "Сайт не открылся при проверке кода. Проверьте ещё раз"


def _store(request: Request, business_id: Any) -> SqlMarking:
    return SqlMarking(_sessions(request), business_id, request.app.state.cipher)


def launch_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.get("/marking")
    async def get_marking(request: Request, business_id: Owned) -> dict[str, Any]:
        found = await _store(request, business_id).get()
        return {"saved": None if found is None else masked(found)}

    # Тело разбираем сами: стандартная ошибка проверки вернула бы ИНН в ответе.
    @router.put("/marking")
    async def put_marking(
        request: Request, business_id: OwnedChecked, raw: Annotated[dict[str, Any], Body()]
    ) -> dict[str, Any]:
        try:
            kind = AdvertiserKind(_text(raw, "kind"))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Выберите: организация, ИП или физическое лицо",
            ) from None
        phone = _text(raw, "phone") or None
        okved = _text(raw, "okved").strip() or None
        marking = Marking(kind, _text(raw, "inn").strip(), _text(raw, "name"), phone, okved)
        problem = await save_marking(_store(request, business_id), marking)
        if problem is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, MARKING_PROBLEMS[problem])
        return await get_marking(request, business_id)

    @router.get("/tag-check")
    async def get_tag_check(request: Request, business_id: Owned) -> dict[str, Any]:
        check = await SqlTagChecks(_sessions(request), business_id).latest()
        return {"check": _check_view(check), "site": await _site(request, business_id)}

    @router.post("/tag-check", status_code=status.HTTP_202_ACCEPTED)
    async def post_tag_check(request: Request, business_id: OwnedChecked) -> dict[str, Any]:
        found = await SqlProfiles(_sessions(request), business_id).get()
        raw = found[1].strip() if found else ""
        if not raw:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "В профиле нет сайта: проверять нечего. Без сайта обращения считаются по звонкам",
            )
        url, problem = check_url(raw, load_site_rules())
        if problem is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, URL_PROBLEMS[problem])
        checks = SqlTagChecks(_sessions(request), business_id)
        try:
            await checks.request(url, load_tag_rules(), _now(request))
        except TooManyChecks:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Сегодня сайт проверяли уже много раз. Попробуйте завтра",
            ) from None
        return {"check": _check_view(await checks.latest()), "site": url}

    @router.get("/launch-check")
    async def launch_check(request: Request, business_id: Owned) -> dict[str, Any]:
        sessions = _sessions(request)
        found = await SqlProfiles(sessions, business_id).get()
        services = len(found[0].services) + len(found[0].features) if found else 0
        plan = await SqlBudgetPlans(sessions, business_id).get()
        accounts = await account_states(sessions, business_id)
        connected = [login for login, state in accounts if state is AccountState.CONNECTED]
        marking = await _store(request, business_id).get()
        site = await _site(request, business_id)
        chosen = await SqlWizardAds(sessions, business_id).chosen()
        counter_ok, counter_text = _counter_item(
            found is not None, site, await SqlTagChecks(sessions, business_id).latest()
        )
        items = [
            {
                "key": "profile",
                "ok": services > 0,
                "text": f"Профиль: фактов для рекламы {services}"
                if services
                else "В профиле нет услуг: рекламу не из чего писать",
            },
            {
                "key": "plan",
                "ok": plan is not None,
                "text": f"Бюджет: {_rub(plan // MICROS_PER_RUB)} в месяц"
                if plan is not None
                else "Бюджет на месяц не задан",
            },
            {
                "key": "account",
                "ok": bool(connected),
                "text": f"Кабинет Директа: {connected[0]}"
                if connected
                else (
                    f"Кабинет {accounts[0][0]} нужно подключить заново"
                    if accounts
                    else "Кабинет Директа не подключён"
                ),
            },
            {
                "key": "marking",
                "ok": marking is not None,
                "text": f"Маркировка: {KINDS[marking.kind]}, ИНН {masked(marking)['inn']}"
                if marking is not None
                else "Нет данных для маркировки: без них запускать рекламу нельзя по закону",
            },
            {"key": "counter", "ok": counter_ok, "text": counter_text},
            {
                "key": "ads",
                "ok": bool(chosen),
                "text": f"Объявления: выбрано {len(chosen)}"
                if chosen
                else "Объявления не выбраны: вернитесь к шагу «Объявления»",
            },
        ]
        return {
            "items": items,
            "ready": all(item["ok"] for item in items),
            "launch_available": False,
            "launch_text": LAUNCH_LATER,
        }

    return router
