"""Демонстрационный кабинет для просмотра интерфейса на локальной машине.

Бизнес вымышленный, площадка не вызывается, деньги не тратятся. Команда отказывается
работать, если сервис настроен не на localhost: на сервере демо-данным не место.
"""

import base64
import json
import os
import random
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.application.service import hash_token
from adhelper.creatives.application.service import cited_facts, profile_from

DEMO_UID = "demo-owner"
DEMO_LOGIN = "lak-studio"
MOSCOW = ZoneInfo("Europe/Moscow")
RUB = 1_000_000
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
SESSION_FILE = ".demo-session"
ENV_FILE = ".demo-env"

SERVICES: list[dict[str, Any]] = [
    {"name": "Маникюр с покрытием", "price": 1500},
    {"name": "Педикюр", "price": 2200},
    {"name": "Наращивание ногтей", "price": 2800},
    {"name": "Снятие покрытия", "price": 300},
]
ADDRESS = "Москва, ул. Профсоюзная, 12"
FEATURES = [
    "Стерильные инструменты из крафт-пакета",
    "Онлайн-запись",
    "5 минут от метро Профсоюзная",
]
# Кампания: название, состояние, дневной лимит в рублях, показы в день, CTR в б.п., цена клика.
CAMPAIGNS = [
    ("101", "Маникюр — поиск", "on", 600, 900, 520, 21),
    ("102", "Педикюр — поиск", "on", 400, 520, 430, 24),
    ("103", "Маникюр — сайты и приложения", "on", 300, 4200, 55, 9),
    ("104", "Наращивание — поиск", "suspended", 300, 380, 260, 34),
]
# Объявления направления: номер, заголовок, текст, состояние, вес в показах направления,
# сколько последних дней не показывается. Тексты только из профиля.
ADS = {
    "101": [
        (
            "1011",
            "Маникюр с покрытием 1500 ₽",
            "Стерильные инструменты из крафт-пакета. Онлайн-запись.",
            "on",
            5,
            0,
        ),
        ("1012", "Маникюр у метро Профсоюзная", "5 минут от метро. Покрытие — 1500 ₽.", "on", 3, 0),
        ("1013", "Маникюр рядом с домом", "Онлайн-запись в пару кликов.", "suspended", 2, 4),
    ],
    "102": [
        ("1021", "Педикюр за 2200 ₽", "Стерильные инструменты из крафт-пакета.", "on", 3, 0),
        ("1022", "Педикюр у метро Профсоюзная", "Онлайн-запись. 5 минут от метро.", "on", 2, 0),
    ],
    "103": [
        ("1031", "Маникюр с покрытием 1500 ₽", "Студия у метро Профсоюзная.", "on", 1, 0),
    ],
    "104": [
        (
            "1041",
            "Наращивание ногтей 2800 ₽",
            "Онлайн-запись. 5 минут от метро.",
            "suspended",
            2,
            0,
        ),
        (
            "1042",
            "Наращивание у метро",
            "Стерильные инструменты из крафт-пакета.",
            "suspended",
            1,
            0,
        ),
    ],
}
# Кому показываем: как настроено направление на площадке.
TARGETING = {
    "101": {
        "placement": "search",
        "regions": ["Москва"],
        "phrases": ["маникюр профсоюзная", "маникюр с покрытием рядом", "маникюр юго-запад"],
        "schedule": "каждый день, 9:00–21:00",
    },
    "102": {
        "placement": "search",
        "regions": ["Москва"],
        "phrases": ["педикюр профсоюзная", "педикюр рядом с метро"],
        "schedule": "каждый день, 9:00–21:00",
    },
    "103": {
        "placement": "network",
        "regions": ["Москва, до 3 км от студии"],
        "phrases": [],
        "schedule": "каждый день, 8:00–22:00",
    },
    "104": {"placement": "search", "regions": ["Москва"], "phrases": ["наращивание ногтей"]},
}
# Объявления, которые система готовит сама: направление, заголовок, текст, состояние,
# причина состояния, можно ли без владельца, повод написать, сколько часов назад написано.
CREATIVES = [
    (
        "101",
        "Маникюр у Профсоюзной",
        "Онлайн-запись. Стерильные инструменты из крафт-пакета.",
        "launch_pending",
        "place_free",
        True,
        "refill",
        3,
    ),
    (
        "101",
        "Снятие покрытия — 300 ₽",
        "Аккуратно снимем старое покрытие. Онлайн-запись.",
        "reserve",
        "no_place",
        True,
        "refill",
        3,
    ),
    (
        "102",
        "Педикюр в студии «Лак»",
        "Педикюр за 2200 ₽. Москва, ул. Профсоюзная, 12.",
        "needs_owner",
        "place_free",
        False,
        "refill",
        20,
    ),
    (
        "102",
        "Педикюр за 2000 ₽",
        "Онлайн-запись. 5 минут от метро.",
        "stopped",
        "fact_changed",
        True,
        "new_campaign",
        26,
    ),
    (
        "101",
        "Маникюр без записи",
        "Приходите в любое время, ул. Профсоюзная, 12.",
        "withdrawn",
        "owner",
        True,
        "refill",
        30,
    ),
]


Run = Callable[..., Awaitable[None]]


class NotLocal(Exception):
    """Демо-данные разрешены только на локальной машине."""


@dataclass(frozen=True)
class DemoResult:
    business_id: uuid.UUID
    created: bool


def require_local(public_base_url: str) -> None:
    if urlsplit(public_base_url).hostname not in LOCAL_HOSTS:
        raise NotLocal


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    path.chmod(0o600)


def write_demo_env(root: Path) -> None:
    """Ключи шифрования для демо: случайные, только в файле с правами владельца."""
    path = root / ENV_FILE
    if path.exists():
        return
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    _write_private(path, f"TOKEN_ENCRYPTION_KEYS=demo:{key}\nCOOKIE_SECURE=false\n")


async def seed_demo(
    sessions: async_sessionmaker[AsyncSession], now: datetime, root: Path, mismatch: bool = False
) -> DemoResult:
    async with sessions.begin() as session:
        # Сценарии — разные владельцы: журнал только дополняется, сбросить кабинет нельзя.
        uid = f"{DEMO_UID}-mismatch" if mismatch else DEMO_UID
        user_id = await session.scalar(
            text("SELECT id FROM users WHERE yandex_uid = :uid"), {"uid": uid}
        )
        created = user_id is None
        if user_id is None:
            user_id = uuid.uuid4()
            business_id = uuid.uuid4()
            await _seed(session, uid, user_id, business_id, now, mismatch)
        else:
            business_id = await session.scalar(
                text("SELECT id FROM businesses WHERE user_id = :u ORDER BY created_at LIMIT 1"),
                {"u": user_id},
            )
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        await session.execute(
            text(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at)"
                " VALUES (:h, :u, :c, :e)"
            ),
            {"h": hash_token(token), "u": user_id, "c": csrf, "e": now + timedelta(days=7)},
        )
    # Токен сессии только в файл: в выводе команды и в логах его нет.
    payload = {"cookie": f"ah_session={token}", "business_id": str(business_id)}
    _write_private(root / SESSION_FILE, json.dumps(payload))
    return DemoResult(business_id, created)


async def _seed(
    session: AsyncSession,
    uid: str,
    user_id: uuid.UUID,
    business_id: uuid.UUID,
    now: datetime,
    mismatch: bool,
) -> None:
    # Активный логин уникален на площадке, поэтому у второго сценария свой.
    login = f"{DEMO_LOGIN}-2" if mismatch else DEMO_LOGIN

    async def run(statement: str, **values: object) -> None:
        await session.execute(text(statement), values)

    await run(
        "INSERT INTO users (id, yandex_uid, email) VALUES (:id, :uid, 'owner@example.test')",
        id=user_id,
        uid=uid,
    )
    await run(
        "INSERT INTO businesses (id, user_id, name) VALUES (:id, :u, 'Студия маникюра «Лак»')",
        id=business_id,
        u=user_id,
    )
    await run(
        "INSERT INTO ad_accounts (id, business_id, platform, external_login, encrypted_access,"
        " encrypted_refresh, token_expires_at, scopes, state)"
        " VALUES (:id, :b, 'yandex_direct', :login, 'demo', 'demo', :exp,"
        " '{direct:api}', 'connected')",
        id=uuid.uuid4(),
        b=business_id,
        login=login,
        exp=now + timedelta(days=365),
    )
    await run(
        "INSERT INTO business_profiles (business_id, services, address, features, niche, website)"
        " VALUES (:b, CAST(:s AS jsonb), :a, CAST(:f AS jsonb), 'маникюр', 'https://lak.example')",
        b=business_id,
        s=json.dumps(SERVICES, ensure_ascii=False),
        a=ADDRESS,
        f=json.dumps(FEATURES, ensure_ascii=False),
    )

    local = now.astimezone(MOSCOW)
    today = local.date()
    month_start = datetime(local.year, local.month, 1, tzinfo=MOSCOW)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    spent_by_day = await _seed_campaigns(run, business_id, login, today)

    period_id = await session.scalar(
        text(
            "INSERT INTO budget_periods (business_id, account, starts_at, ends_at,"
            " hard_limit_micros, reserve_bp, rules_version, next_check_at)"
            " VALUES (:b, :login, :s, :e, :limit, 1000, 'budget-1', :n) RETURNING id"
        ),
        {
            "b": business_id,
            "login": login,
            "s": month_start,
            "e": next_month,
            "limit": 40_000 * RUB,
            "n": now + timedelta(minutes=5),
        },
    )
    spent = 0
    for day, cost in sorted(spent_by_day.items()):
        if day < month_start.date():
            continue
        spent += cost
        as_of = min(datetime.combine(day, datetime.max.time(), MOSCOW), now - timedelta(hours=1))
        await run(
            "INSERT INTO spend_snapshots (period_id, reported_micros, data_as_of)"
            " VALUES (:p, :r, :t)",
            p=period_id,
            r=spent,
            t=as_of,
        )
    # Отсчёт сверки снят в начале периода. 140 ₽ укладываются в допуск (2% лимита = 800 ₽),
    # 1 240 ₽ — нет: страж остановил рекламу и ждёт владельца.
    gap = (1_240 if mismatch else 140) * RUB
    await run(
        "UPDATE budget_periods SET opening_balance_micros = :ob, opening_spent_micros = 0,"
        " last_balance_micros = :lb, balance_checked_at = :t WHERE id = :p",
        ob=60_000 * RUB,
        lb=60_000 * RUB - spent - gap,
        t=now - timedelta(minutes=4),
        p=period_id,
    )
    await _seed_history(run, business_id, login, now)
    if mismatch:
        await _seed_mismatch(run, business_id, login, now, gap)
    await _seed_creatives(run, business_id, login, now)


async def _seed_campaigns(
    run: Run, business_id: uuid.UUID, login: str, today: date
) -> dict[date, int]:
    noise = random.Random(7)
    spent: dict[date, int] = {}
    for campaign_id, name, state, limit, shows, ctr_bp, cpc in CAMPAIGNS:
        await run(
            "INSERT INTO campaigns (business_id, account, campaign_id, name, state, limit_micros,"
            " targeting) VALUES (:b, :login, :c, :n, :s, :l, CAST(:t AS jsonb))",
            b=business_id,
            login=login,
            c=campaign_id,
            n=name,
            s=state,
            l=limit * RUB,
            t=json.dumps(TARGETING[campaign_id], ensure_ascii=False),
        )
        for ad_id, title, body, ad_state, *_ in ADS[campaign_id]:
            await run(
                "INSERT INTO ads (business_id, account, ad_id, campaign_id, title, text, href,"
                " state) VALUES (:b, :login, :a, :c, :t, :x, 'https://lak.example', :s)",
                b=business_id,
                login=login,
                a=ad_id,
                c=campaign_id,
                t=title,
                x=body,
                s=ad_state,
            )
        for back in range(30):
            day = today - timedelta(days=back)
            # Приостановленная кампания последние 6 дней не крутится.
            if state == "suspended" and back < 6:
                continue
            weekend = 1.25 if day.weekday() >= 5 else 1.0
            impressions = int(shows * weekend * noise.uniform(0.75, 1.2))
            clicks = impressions * ctr_bp * noise.randint(80, 120) // 1_000_000
            cost = min(clicks * cpc * RUB * noise.randint(85, 115) // 100, limit * RUB)
            conversions = clicks * noise.randint(4, 11) // 100
            await run(
                "INSERT INTO campaign_stats (business_id, account, campaign_id, day,"
                " impressions, clicks, cost_micros, conversions)"
                " VALUES (:b, :login, :c, :d, :i, :k, :m, :v)",
                b=business_id,
                login=login,
                c=campaign_id,
                d=day,
                i=impressions,
                k=clicks,
                m=cost,
                v=conversions,
            )
            await _split_by_ads(
                run,
                business_id,
                login,
                campaign_id,
                day,
                back,
                noise,
                (impressions, clicks, cost, conversions),
            )
            spent[day] = spent.get(day, 0) + cost
    return spent


async def _split_by_ads(
    run: Run,
    business_id: uuid.UUID,
    login: str,
    campaign_id: str,
    day: date,
    back: int,
    noise: random.Random,
    totals: tuple[int, int, int, int],
) -> None:
    """Делит день направления между объявлениями так, что суммы совпадают с отчётом кампании."""
    showing = [ad[0] for ad in ADS[campaign_id] if back >= ad[5]]
    weights = [ad[4] * noise.randint(80, 120) for ad in ADS[campaign_id] if back >= ad[5]]
    parts = [[total * weight // sum(weights) for total in totals] for weight in weights]
    # Остаток от деления достаётся самому крупному объявлению, а не последнему.
    biggest = weights.index(max(weights))
    for k, total in enumerate(totals):
        parts[biggest][k] += total - sum(part[k] for part in parts)
    for ad_id, part in zip(showing, parts, strict=True):
        await run(
            "INSERT INTO ad_stats (business_id, account, ad_id, day,"
            " impressions, clicks, cost_micros, conversions)"
            " VALUES (:b, :login, :a, :d, :i, :k, :m, :v)",
            b=business_id,
            login=login,
            a=ad_id,
            d=day,
            i=part[0],
            k=part[1],
            m=part[2],
            v=part[3],
        )


async def _seed_history(run: Run, business_id: uuid.UUID, login: str, now: datetime) -> None:
    changes = [
        (
            6,
            "autopilot",
            "pause",
            "104",
            {"state": "on"},
            {"state": "suspended"},
            "Наращивание — поиск: 14 дней, 1 обращение при расходе 2 950 ₽. Приостановлено.",
        ),
        (
            3,
            "budget_guard",
            "set_limit",
            "103",
            {"limit": 450 * RUB},
            {"limit": 300 * RUB},
            "Сайты и приложения: лимит снижен с 450 до 300 ₽ в день, чтобы уложиться в бюджет.",
        ),
        (
            3,
            "budget_guard",
            "set_limit",
            "101",
            {"limit": 450 * RUB},
            {"limit": 600 * RUB},
            "Маникюр — поиск: лимит поднят с 450 до 600 ₽ после снижения у сайтов и приложений.",
        ),
        (
            1,
            "user",
            "set_limit",
            "102",
            {"limit": 350 * RUB},
            {"limit": 400 * RUB},
            "Педикюр — поиск: лимит 400 ₽ в день по решению владельца.",
        ),
    ]
    for index, (days, source, kind, target, before, after, explanation) in enumerate(changes):
        await run(
            "INSERT INTO changes (business_id, account, source, kind, target_ref, before, planned,"
            " after, status, explanation, idempotency_key, created_at, updated_at)"
            " VALUES (:b, :login, :s, :k, :t, CAST(:bf AS jsonb), CAST(:af AS jsonb),"
            " CAST(:af AS jsonb), 'applied', :e, :key, :at, :at)",
            b=business_id,
            login=login,
            s=source,
            k=kind,
            t=target,
            bf=json.dumps(before),
            af=json.dumps(after),
            e=explanation,
            key=f"demo:{index}",
            at=now - timedelta(days=days, hours=index),
        )
    decisions = [
        (6, "campaign:104", "inefficient", "pause", {"cost_micros": 2_950 * RUB, "conversions": 1}),
        (2, "campaign:101", "efficient", None, {"cost_micros": 4_800 * RUB, "conversions": 21}),
        (2, "campaign:103", "low_data", None, {"cost_micros": 1_900 * RUB, "conversions": 2}),
    ]
    for days, target, verdict, action, inputs in decisions:
        await run(
            "INSERT INTO decisions (business_id, account, object_ref, verdict, action, status,"
            " inputs, rules_version, decided_at)"
            " VALUES (:b, :login, :t, :v, :a, 'active', CAST(:i AS jsonb), 'autopilot-1', :at)",
            b=business_id,
            login=login,
            t=target,
            v=verdict,
            a=action,
            i=json.dumps(inputs),
            at=now - timedelta(days=days, hours=8),
        )


async def _seed_mismatch(
    run: Run, business_id: uuid.UUID, login: str, now: datetime, gap: int
) -> None:
    at = now - timedelta(minutes=4)
    await run(
        "INSERT INTO alerts (business_id, kind, details, created_at)"
        " VALUES (:b, 'balance_mismatch', CAST(:d AS jsonb), :t)",
        b=business_id,
        d=json.dumps({"gap_micros": gap, "account": login}),
        t=at,
    )
    for campaign_id, name, state, *_ in CAMPAIGNS:
        if state != "on":
            continue
        await run(
            "UPDATE campaigns SET state = 'suspended' WHERE business_id = :b AND campaign_id = :c",
            b=business_id,
            c=campaign_id,
        )
        await run(
            "INSERT INTO changes (business_id, account, source, kind, target_ref, before, planned,"
            " after, status, explanation, idempotency_key, created_at, updated_at)"
            " VALUES (:b, :login, 'budget_guard', 'pause', :c, '{\"state\": \"on\"}',"
            ' \'{"state": "suspended"}\', \'{"state": "suspended"}\', \'applied\', :e,'
            " :key, :at, :at)",
            b=business_id,
            login=login,
            c=campaign_id,
            e=f"{name}: остановлено, со счёта ушло больше, чем показывает статистика.",
            key=f"demo:stop:{campaign_id}",
            at=at,
        )


async def _seed_creatives(run: Run, business_id: uuid.UUID, login: str, now: datetime) -> None:
    profile = profile_from(
        "Студия маникюра «Лак»",
        [(s["name"], s["price"]) for s in SERVICES],
        ADDRESS,
        FEATURES,
        "маникюр",
    )
    for campaign_id, headline, body, state, why, auto_ok, reason, hours in CREATIVES:
        written = now - timedelta(hours=hours)
        placed = written + timedelta(minutes=5)
        history = [{"at": written.isoformat(), "state": "ready", "reason": "written"}]
        if state != "ready":
            history.append({"at": placed.isoformat(), "state": state, "reason": why})
        sources = cited_facts(profile, headline, body)
        await run(
            "INSERT INTO creatives (business_id, account, campaign_id, headline, text, source,"
            " auto_ok, reason, facts, sources, state, state_reason, history, created_at,"
            " updated_at) VALUES (:b, :login, :c, :h, :x, 'model', :ok, :r, CAST(:f AS jsonb),"
            " CAST(:src AS jsonb), :s, :why, CAST(:hist AS jsonb), :at, :up)",
            b=business_id,
            login=login,
            c=campaign_id,
            h=headline,
            x=body,
            ok=auto_ok,
            r=reason,
            f=json.dumps([s.key for s in sources], ensure_ascii=False),
            src=json.dumps([asdict(s) for s in sources], ensure_ascii=False),
            s=state,
            why=why,
            hist=json.dumps(history, ensure_ascii=False),
            at=written,
            up=placed,
        )
    # Для направления с одним объявлением прямо сейчас пишутся ещё два.
    await run(
        "INSERT INTO creative_sets (business_id, status, rules_version, attempts, drafts,"
        " rejected, notes, tokens_used, needs_review, created_at, started_at, account,"
        " campaign_id, reason, wanted) VALUES (:b, 'running', 'creatives-2', 1, '[]', '[]',"
        " '[]', 0, false, :c, :c, :login, '103', 'refill', 2)",
        b=business_id,
        login=login,
        c=now - timedelta(minutes=2),
    )
