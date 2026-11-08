"""Мастер запуска после входа: маркировка, код статистики, готовность и сам запуск.

Запуск создаёт период бюджета и кампании через журнал. Без площадки (клиент Директа — T5)
он честно недоступен: сводка говорит, что всё сохранено и запуск продолжится с этого места.
"""

import logging
import re
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, status

from adhelper.accounts.adapters.sql import SqlMarking, account_states
from adhelper.accounts.application.marking import (
    AdvertiserKind,
    Marking,
    MarkingProblem,
    SharedAccountMarkingMismatch,
    mask_inn,
    masked,
    save_marking,
)
from adhelper.accounts.application.service import AccountState
from adhelper.api.cabinet import Owned, OwnedChecked, _now, _sessions
from adhelper.api.control import MICROS_PER_RUB, _ledger
from adhelper.api.plan import _rub
from adhelper.api.site import URL_PROBLEMS
from adhelper.budget.adapters.rules_file import load_rules as load_budget_rules
from adhelper.budget.adapters.sql import SqlBudgetPlans
from adhelper.business.adapters.rules_file import load_site_rules, load_tag_rules
from adhelper.business.adapters.tag_checks import SqlTagChecks, TooManyChecks
from adhelper.business.application.site import check_url
from adhelper.business.application.tag import StoredTagCheck, TagStatus
from adhelper.campaigns.adapters.launch_sql import SqlLaunches
from adhelper.campaigns.adapters.rules_file import load_launch_rules
from adhelper.campaigns.application.launch import (
    MOSCOW_TZ,
    PLACEMENT_WORDS,
    Advertiser,
    LaunchInputs,
    LaunchPlan,
    LaunchProblem,
    LaunchResult,
    LaunchStatus,
    NewAd,
    UnregisteredAdvertiserError,
    expand_keywords_with_model,
    launch,
    launch_state,
    preview,
    region_of,
)
from adhelper.creatives.adapters.sql import SqlProfiles
from adhelper.creatives.adapters.wizard import SqlWizardAds
from adhelper.platforms.adapters.direct.transport import DirectGeoRegionAmbiguous
from adhelper.stats.adapters.sql import SqlDashboard

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

logger = logging.getLogger(__name__)


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
LAUNCH_NOT_READY = "Когда всё в списке будет готово, здесь появится кнопка запуска."
LAUNCH_NOW = (
    "Нажмите «Запустить рекламу»: сервис создаст кампании, лимит встанет в самом Яндексе."
    " Показы начнутся, когда Яндекс проверит объявления."
)
LAUNCH_DONE = (
    "Реклама запущена: Яндекс проверяет объявления, обычно это несколько часов."
    " Остановить показы можно паузой в шапке кабинета."
)
LAUNCH_PENDING = (
    "Запуск начат, но Яндекс пока не подтвердил создание кампаний."
    " Сервис проверит сам и не создаст их дважды. Можно нажать «Запустить рекламу» ещё раз."
)
# Почему журнал не дал создать кампанию: слова для владельца.
REFUSALS = {
    "period_exists": "В этом кабинете реклама уже идёт с бюджетом на месяц. Новый запуск создал"
    " бы второй бюджет; меняйте настройки в кабинете",
    "exceeds_operating_budget": "В кабинете Яндекса уже есть кампании с лимитами: вместе с новыми"
    " они превысили бы ваш бюджет. Остановите или удалите лишние в Яндексе и запустите снова",
    "budget_stop_pending": "Сейчас сработала защита бюджета: запуск подождёт её решения",
    "no_active_period": "Бюджет месяца не начался: попробуйте запустить ещё раз",
    "ends_after_period": "Срок кампании вышел за бюджетный период: попробуйте запустить ещё раз",
}


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
        return True, (
            "Код статистики можно проверить после добавления сайта; запуск от этого не зависит"
        )
    if not site:
        return True, f"{NO_SITE}; Метрика для запуска не обязательна"
    if check is None or check.url != site:
        return True, (
            "Код Метрики ещё не проверен; запуск возможен, "
            "но обращения на сайте могут не учитываться"
        )
    if check.status is TagStatus.FOUND:
        numbers = ", ".join(str(n) for n in check.counters)
        return True, f"Код Метрики на сайте есть, счётчик {numbers}"
    if check.status in (TagStatus.QUEUED, TagStatus.RUNNING):
        return True, (
            "Проверяю код Метрики на сайте; запуск возможен, обращения могут не учитываться"
        )
    if check.status is TagStatus.MISSING:
        return True, (
            "Кода Метрики на главной странице не нашла; запуск возможен, "
            "обращения могут не учитываться"
        )
    return True, "Сайт не открылся при проверке Метрики; можно запустить рекламу и проверить позже"


def _store(request: Request, business_id: Any) -> SqlMarking:
    return SqlMarking(_sessions(request), business_id, request.app.state.cipher)


def launch_router() -> APIRouter:
    router = APIRouter(prefix="/api/businesses/{business_id}")

    @router.get("/marking")
    async def get_marking(request: Request, business_id: Owned) -> dict[str, Any]:
        found = await _store(request, business_id).get()
        if found is not None:
            return {"saved": masked(found)}
        sessions = _sessions(request)
        accounts = await account_states(sessions, business_id)
        connected = [login for login, state in accounts if state is AccountState.CONNECTED]
        if connected:
            login = connected[0]
            platform = request.app.state.platform_for(business_id, login)
            if platform and hasattr(platform, "read_advertiser"):
                try:
                    adv = await platform.read_advertiser(login)
                    if adv and adv.inn:
                        phone = adv.phone
                        masked_phone = (
                            None
                            if not phone
                            else f"+7 ••• •••-{phone[-4:-2]}-{phone[-2:]}"
                            if len(phone) >= 4
                            else "••••"
                        )
                        return {
                            "saved": {
                                "kind": adv.kind,
                                "inn": mask_inn(adv.inn),
                                "name": adv.name,
                                "phone": masked_phone,
                                "okved": adv.okved,
                            }
                        }
                except Exception as error:
                    logger.warning("Не удалось прочитать реквизиты из Директа: %s", error)
        return {"saved": None}

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
        try:
            problem = await save_marking(_store(request, business_id), marking)
        except SharedAccountMarkingMismatch:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "В этом кабинете уже указан другой рекламодатель. "
                "Для другого ИНН подключите отдельный кабинет.",
            ) from None
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

    async def readiness(
        request: Request, business_id: Any
    ) -> tuple[list[dict[str, Any]], LaunchInputs | None, str | None]:
        sessions = _sessions(request)
        found = await SqlProfiles(sessions, business_id).get()
        services = len(found[0].services) + len(found[0].features) if found else 0
        plan = await SqlBudgetPlans(sessions, business_id).get()
        accounts = await account_states(sessions, business_id)
        connected = [login for login, state in accounts if state is AccountState.CONNECTED]
        login = connected[0] if connected else None
        platform = request.app.state.platform_for(business_id, login) if login else None
        adv_info: Advertiser | None = None
        if platform and login and hasattr(platform, "read_advertiser"):
            try:
                adv_info = await platform.read_advertiser(login)
            except Exception as error:
                logger.warning("Не удалось прочитать рекламодателя из Директа: %s", error)
        marking = await _store(request, business_id).get()
        if (adv_info is None or not adv_info.inn) and marking is not None:
            adv_info = Advertiser(
                marking.kind.value, marking.inn, marking.name, marking.phone, marking.okved
            )
        marking_ok = adv_info is not None and bool(adv_info.inn)
        kind_label = (
            KINDS.get(AdvertiserKind(adv_info.kind), adv_info.kind)
            if adv_info and adv_info.kind in AdvertiserKind._value2member_map_
            else (adv_info.kind if adv_info else "")
        )
        inn_str = mask_inn(adv_info.inn) if (adv_info and adv_info.inn) else ""
        marking_text = (
            f"Маркировка: {kind_label}, ИНН {inn_str}"
            if marking_ok
            else "В кабинете Директа не указаны реквизиты для маркировки рекламы (ИНН)"
        )
        site = await _site(request, business_id)
        chosen = await SqlWizardAds(sessions, business_id).chosen()
        tag = await SqlTagChecks(sessions, business_id).latest()
        counter_ok, counter_text = _counter_item(found is not None, site, tag)
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
                "ok": marking_ok,
                "text": marking_text,
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
        if not all(item["ok"] for item in items) or found is None or plan is None or not login:
            return items, None, login
        counters = (
            tag.counters
            if tag is not None and tag.url == site and tag.status is TagStatus.FOUND
            else ()
        )
        address = found[0].address
        city_facts = [
            feature
            for feature in found[0].features
            if re.match(r"^\s*(?:г(?:ород)?\.?\s+)\S", feature, re.IGNORECASE)
        ]
        if len(city_facts) == 1 and city_facts[0].casefold() not in address.casefold():
            address = ", ".join(part for part in (address, city_facts[0]) if part)
        inputs = LaunchInputs(
            title=found[0].name,
            account=login,
            monthly=plan,
            ads=tuple(NewAd(draft.headline, draft.text) for draft in chosen),
            href=site,
            address=address,
            counters=tuple(counters),
        )
        launch_rules = load_launch_rules()
        mentioned_cities = {
            f"region:{region_id}"
            for city in launch_rules.cities
            for region_id in (launch_rules.cities[city],)
            if re.search(rf"(?<!\w){re.escape(city)}(?!\w)", address, re.IGNORECASE)
        }
        explicit_cities: set[str] = set()
        for match in re.findall(r"(?:г(?:ород)?\.?\s*)([^,;]+)", address, re.IGNORECASE):
            normalized = match.strip().casefold().replace("ё", "е")
            recognized = {
                f"region:{region_id}"
                for city in launch_rules.cities
                for region_id in (launch_rules.cities[city],)
                if re.search(rf"(?<!\w){re.escape(city)}(?!\w)", normalized, re.IGNORECASE)
            }
            explicit_cities.update(recognized or {f"unknown:{normalized}"})
        if len(mentioned_cities | explicit_cities) > 1:
            items.append(
                {
                    "key": "launch_plan",
                    "ok": False,
                    "text": (
                        "В адресе профиля указано несколько городов. Оставьте один город "
                        "и регион, чтобы не запустить рекламу не там."
                    ),
                }
            )
            return items, None, login
        known_region = region_of(address, launch_rules)
        if known_region is not None:
            known_city = next(
                (
                    city
                    for city in sorted(launch_rules.cities, key=len, reverse=True)
                    if launch_rules.cities[city] == known_region
                    and city.casefold().replace("ё", "е") in address.casefold().replace("ё", "е")
                ),
                "",
            )
            inputs = replace(inputs, region_id=known_region, city_name=known_city)
        elif platform is not None and hasattr(platform, "resolve_city_region"):
            try:
                resolved = await platform.resolve_city_region(login, address)
            except DirectGeoRegionAmbiguous:
                items.append(
                    {
                        "key": "launch_plan",
                        "ok": False,
                        "text": (
                            "Нашёл несколько городов с таким названием. Уточните город и область "
                            "в адресе профиля."
                        ),
                    }
                )
                return items, None, login
            except Exception as error:
                logger.warning(
                    "Не удалось проверить город в справочнике Директа: %s",
                    type(error).__name__,
                )
                items.append(
                    {
                        "key": "launch_plan",
                        "ok": False,
                        "text": (
                            "Не удалось проверить город в Яндекс Директе. "
                            "Нажмите «Проверить снова»."
                        ),
                    }
                )
                return items, None, login
            if resolved is not None:
                inputs = replace(inputs, region_id=resolved[0], city_name=resolved[1])
        budget_rules = load_budget_rules("budget-1")
        now = _now(request)
        preflight = preview(inputs, launch_rules, budget_rules, now)
        if preflight.problem is not None:
            items.append(_plan_item(preflight, inputs.city_name))
            return items, None, login
        model = getattr(request.app.state, "text_model", None) or getattr(
            request.app.state, "command_model", None
        )
        keywords = await expand_keywords_with_model(
            model=model,
            services=found[0].services,
            niche=found[0].niche,
            address=found[0].address,
        )
        inputs = replace(inputs, keywords=keywords)
        planned = preview(inputs, launch_rules, budget_rules, now)
        items.append(_plan_item(planned, inputs.city_name))
        return items, inputs if planned.problem is None else None, login

    @router.get("/launch-check")
    async def launch_check(request: Request, business_id: Owned) -> dict[str, Any]:
        items, inputs, login = await readiness(request, business_id)
        state = await _state(request, business_id)
        platform = request.app.state.platform_for(business_id, login) if login else None
        launched = state.status.value if state is not None else None
        available = (
            inputs is not None
            and platform is not None
            and launched in (None, LaunchStatus.PENDING.value)
        )
        if launched == LaunchStatus.DONE.value:
            text = LAUNCH_DONE
        elif launched == LaunchStatus.PENDING.value:
            text = LAUNCH_PENDING
        elif platform is None:
            text = LAUNCH_LATER
        elif inputs is None:
            text = LAUNCH_NOT_READY
        else:
            text = LAUNCH_NOW
        return {
            "items": items,
            "ready": all(item["ok"] for item in items),
            "launch_available": available,
            "launched": launched,
            "launch_text": text,
        }

    @router.post("/launch")
    async def post_launch(request: Request, business_id: OwnedChecked) -> dict[str, Any]:
        items, inputs, _login = await readiness(request, business_id)
        missing = [item["text"] for item in items if not item["ok"]]
        if missing or inputs is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Запустить пока нельзя: {missing[0] if missing else 'не хватает данных'}",
            )
        platform = request.app.state.platform_for(business_id, inputs.account)
        if platform is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LAUNCH_LATER)
        marking_store = _store(request, business_id)

        async def advertiser() -> Advertiser | None:
            if platform and hasattr(platform, "read_advertiser"):
                try:
                    adv = await platform.read_advertiser(inputs.account)
                    if adv and adv.inn:
                        return Advertiser(
                            str(adv.kind), str(adv.inn), str(adv.name), adv.phone, adv.okved
                        )
                except Exception as error:
                    logger.warning("Не удалось прочитать рекламодателя при запуске: %s", error)
            found = await marking_store.get()
            if found is None:
                return None
            return Advertiser(found.kind.value, found.inn, found.name, found.phone, found.okved)

        current = await advertiser()
        try:
            result = await launch(
                SqlLaunches(_sessions(request), business_id),
                _ledger(request, business_id, platform, advertiser),
                inputs,
                load_launch_rules(),
                load_budget_rules("budget-1"),
                _now(request),
                advertiser=None if current is None else current.card(),
                require_advertiser=True,
            )
        except UnregisteredAdvertiserError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        return await _answer(request, business_id, inputs.account, result)

    return router


def _plan_item(planned: LaunchPlan, city_name: str = "") -> dict[str, Any]:
    if planned.problem is LaunchProblem.BELOW_PLATFORM_MIN:
        minimum = _rub((planned.minimum_monthly or 0) // MICROS_PER_RUB)
        text = (
            "Бюджета после резерва 10% не хватает на минимум Яндекса 50 ₽ в день."
            f" Поднимите бюджет до {minimum} в месяц"
        )
        return {"key": "launch_plan", "ok": False, "text": text}
    if planned.problem is LaunchProblem.NO_CITY:
        text = (
            "Не нашёл город в адресе профиля или справочнике Директа: "
            "укажите город и регион в адресе профиля"
        )
        return {"key": "launch_plan", "ok": False, "text": text}
    if planned.problem is LaunchProblem.NO_SITE:
        text = "Пока запускаю только с сайтом: объявлению нужна страница, куда ведёт ссылка"
        return {"key": "launch_plan", "ok": False, "text": text}
    if planned.problem is LaunchProblem.NO_ADS:
        return {"key": "launch_plan", "ok": False, "text": "Объявлений для запуска нет"}
    first = planned.campaigns[0]
    until = first.ends_at.astimezone(MOSCOW_TZ).strftime("%d.%m")
    parts = ", ".join(
        f"{PLACEMENT_WORDS[c.placement]} — {_rub(c.limit // MICROS_PER_RUB)}"
        for c in planned.campaigns
    )
    count = len(planned.campaigns)
    word = "кампания" if count == 1 else "кампании"
    return {
        "key": "launch_plan",
        "ok": True,
        "text": f"Запуск: {count} {word} ({parts}) до {until}, 10% бюджета в резерве"
        + (f". Город показа: {city_name}" if city_name else ""),
    }


async def _state(request: Request, business_id: Any) -> LaunchResult | None:
    """Состояние запуска по журналу. Без площадки журнал читается так же: площадку не трогаем."""
    sessions = _sessions(request)
    store = SqlLaunches(sessions, business_id)
    stored = await store.get()
    if stored is None:
        return None
    platform = request.app.state.platform_for(business_id, stored.account)
    return await launch_state(store, _ledger(request, business_id, platform))


async def _answer(
    request: Request, business_id: Any, account: str, result: LaunchResult
) -> dict[str, Any]:
    if result.status is LaunchStatus.PROBLEM:
        raise HTTPException(status.HTTP_409_CONFLICT, "Запустить пока нельзя: проверьте сводку")
    if result.status is LaunchStatus.REFUSED:
        text = REFUSALS.get(result.reason or "", "Проверка бюджета не дала создать кампании")
        raise HTTPException(status.HTTP_409_CONFLICT, text)
    if result.status is LaunchStatus.PENDING:
        return {"status": result.status.value, "campaigns": [], "text": LAUNCH_PENDING}
    dashboard = SqlDashboard(_sessions(request), business_id)
    for created in result.created:
        await dashboard.note_created_campaign(
            account,
            created.campaign_id,
            created.name,
            created.limit,
            [
                (ad_id, ad.headline, ad.text, created.href)
                for ad_id, ad in zip(created.ad_ids, created.ads, strict=False)
            ],
        )
    return {
        "status": result.status.value,
        "campaigns": [
            {"name": c.name, "limit_rubles": c.limit // MICROS_PER_RUB} for c in result.created
        ],
        "text": LAUNCH_DONE,
    }
