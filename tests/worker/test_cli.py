"""Команды оператора: `adhelper check` и `adhelper emergency-stop`."""

import base64
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.budget.domain.limits import CampaignBudget
from adhelper.business.adapters.browser import browser_starts
from adhelper.config import Settings
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus
from adhelper.worker import cli
from adhelper.worker.cli import CheckStatus, main, run_checks, run_emergency_stop
from adhelper.worker.runtime import Runtime
from tests.budget.fake_platform import FakePlatform, Fault
from tests.seed import seed_account

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
BUSINESS = uuid.uuid4()
OTHER = uuid.uuid4()
KEY = base64.b64encode(b"k" * 32).decode()
Sessions = async_sessionmaker[AsyncSession]


def _secret(settings: Settings, value: str) -> Settings:
    return settings.model_copy(update={"token_encryption_keys": SecretStr(value)})


def statuses(results: list) -> dict[str, CheckStatus]:  # type: ignore[type-arg]
    return {result.name: result.status for result in results}


@pytest.fixture(autouse=True)
def browser_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Запуск настоящего браузера проверяют test_browser.py и тест ниже, здесь он лишний."""

    async def starts() -> bool:
        return True

    monkeypatch.setattr(cli, "browser_starts", starts)


async def test_configured_service_passes(migrated: Settings, engine: AsyncEngine) -> None:
    configured = _secret(migrated, f"main:{KEY}").model_copy(
        update={
            "public_base_url": "https://ads.example.ru",
            "text_model_api_key": SecretStr("key"),
            "text_model_project": "folder",
            "text_model_name": "gpt://folder/yandexgpt/latest",
            "judge_model_name": "gpt://folder/yandexgpt-lite/latest",
            "smtp_host": "smtp.yandex.ru",
            "smtp_from": "alerts@ads.example.ru",
            "telegram_bot_token": SecretStr("1:token"),
            "telegram_bot_name": "adhelper_bot",
        }
    )
    results = await run_checks(configured)
    assert all(result.status is CheckStatus.OK for result in results), results


async def test_ads_waiting_over_an_hour_are_reported(
    migrated: Settings, sessions: Sessions, engine: AsyncEngine
) -> None:
    await seed_account(sessions, BUSINESS)
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO creative_sets (business_id, status, rules_version, attempts, drafts,"
                " rejected, notes, tokens_used, needs_review, created_at)"
                " VALUES (:b, 'queued', 'creatives-2', 0, '[]', '[]', '[]', 0, false,"
                " now() - interval '2 hours')"
            ),
            {"b": BUSINESS},
        )
    results = await run_checks(migrated)
    stuck = next(r for r in results if r.name == "ads_writing")
    assert stuck.status is CheckStatus.WARN and ": 1." in stuck.detail


async def test_missing_keys_fail_check(migrated: Settings, engine: AsyncEngine) -> None:
    results = statuses(await run_checks(migrated))
    assert results["encryption_keys"] is CheckStatus.FAIL


async def test_bad_key_is_reported_without_its_value(
    migrated: Settings, engine: AsyncEngine
) -> None:
    results = await run_checks(_secret(migrated, "main:секретное-значение"))
    assert statuses(results)["encryption_keys"] is CheckStatus.FAIL
    assert "секретное-значение" not in repr(results)


async def test_plain_http_is_a_warning_not_a_failure(
    migrated: Settings, engine: AsyncEngine
) -> None:
    results = statuses(await run_checks(_secret(migrated, f"main:{KEY}")))
    assert results["public_url"] is CheckStatus.WARN


async def test_unapplied_migrations_fail_check(
    migrated: Settings, engine: AsyncEngine, sessions: Sessions
) -> None:
    async with sessions.begin() as session:
        await session.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    assert statuses(await run_checks(migrated))["migrations"] is CheckStatus.FAIL


async def test_unreachable_database_hides_password(migrated: Settings) -> None:
    broken = migrated.model_copy(update={"postgres_port": 1})
    results = await run_checks(broken)
    assert statuses(results)["database"] is CheckStatus.FAIL
    assert broken.postgres_password.get_secret_value() not in repr(results)


async def test_bad_model_address_fails_check(migrated: Settings, engine: AsyncEngine) -> None:
    broken = migrated.model_copy(update={"text_model_base_url": "openrouter"})
    assert statuses(await run_checks(broken))["text_model"] is CheckStatus.FAIL


async def test_missing_model_key_is_a_warning(migrated: Settings, engine: AsyncEngine) -> None:
    assert statuses(await run_checks(migrated))["text_model"] is CheckStatus.WARN


async def test_judge_off_by_default_is_a_warning(migrated: Settings, engine: AsyncEngine) -> None:
    configured = _secret(migrated, f"main:{KEY}").model_copy(
        update={"text_model_api_key": SecretStr("key"), "text_model_name": "m"}
    )
    assert statuses(await run_checks(configured))["judge"] is CheckStatus.WARN


async def test_channels_off_by_default_are_warnings(
    migrated: Settings, engine: AsyncEngine
) -> None:
    results = statuses(await run_checks(migrated))
    assert results["email"] is CheckStatus.WARN
    assert results["telegram"] is CheckStatus.WARN


async def test_bot_without_name_fails_and_hides_token(
    migrated: Settings, engine: AsyncEngine
) -> None:
    # Имя бота убираем явно: в локальном .env оно может быть задано.
    broken = migrated.model_copy(
        update={"telegram_bot_token": SecretStr("1:secret-bot"), "telegram_bot_name": ""}
    )
    results = await run_checks(broken)
    assert statuses(results)["telegram"] is CheckStatus.FAIL
    assert "secret-bot" not in repr(results)


def test_check_exit_code_reflects_failure(
    migrated: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check"]) == 1
    assert "encryption_keys" in capsys.readouterr().out


def platform() -> FakePlatform:
    return FakePlatform(
        [
            CampaignBudget("c1", 500, 0, True, None),
            CampaignBudget("c2", 500, 0, True, None),
            CampaignBudget("c3", 500, 0, False, None),
        ],
        NOW,
    )


def runtime(sessions: Sessions, engine: AsyncEngine, fake: FakePlatform | None) -> Runtime:
    return Runtime(sessions, engine, lambda business, account: fake, lambda: NOW)


async def test_stop_pauses_every_active_campaign_through_ledger(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    fake = platform()

    report = await run_emergency_stop(runtime(sessions, engine, fake), BUSINESS)

    assert report.complete
    assert sorted(report.stopped) == ["acc:c1", "acc:c2"]
    assert not any(campaign.active for campaign in fake.campaigns.values())
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert [(r.kind, r.source, r.status) for r in rows] == [
        (ChangeKind.PAUSE, ChangeSource.USER, ChangeStatus.APPLIED)
    ] * 2
    assert all(r.before == {"limit": 500, "active": True} for r in rows)


async def test_repeated_stop_changes_nothing(sessions: Sessions, engine: AsyncEngine) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    fake = platform()
    await run_emergency_stop(runtime(sessions, engine, fake), BUSINESS)

    report = await run_emergency_stop(runtime(sessions, engine, fake), BUSINESS)

    assert report.complete and not report.stopped
    assert len(await SqlChangeRepository(sessions, BUSINESS).all()) == 2


async def test_stop_touches_only_this_business(sessions: Sessions, engine: AsyncEngine) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    await seed_account(sessions, OTHER, "foreign")
    seen: list[tuple[uuid.UUID, str]] = []
    fake = platform()

    def platform_for(business: uuid.UUID, account: str) -> FakePlatform:
        seen.append((business, account))
        return fake

    await run_emergency_stop(Runtime(sessions, engine, platform_for, lambda: NOW), BUSINESS)

    assert {item for item in seen} == {(BUSINESS, "acc")}


async def test_one_failed_campaign_does_not_stop_the_others_and_is_reported(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    fake = platform()
    # Все попытки первой кампании падают; вторая обязана остановиться.
    fake.faults.extend(Fault("pause_campaigns") for _ in range(3))

    report = await run_emergency_stop(runtime(sessions, engine, fake), BUSINESS)

    assert not report.complete
    assert fake.campaigns["c1"].active is True
    assert fake.campaigns["c2"].active is False
    assert report.stopped == ["acc:c2"]
    assert report.failed == [("acc:c1", "still_active")]


async def test_failure_after_apply_is_seen_as_stopped_by_reading(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await seed_account(sessions, BUSINESS, "acc")
    fake = platform()
    fake.faults.append(Fault("pause_campaigns", after_apply=True))

    report = await run_emergency_stop(runtime(sessions, engine, fake), BUSINESS)

    assert not any(campaign.active for campaign in fake.campaigns.values())
    assert report.complete


async def test_without_platform_says_it_stopped_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    await seed_account(sessions, BUSINESS, "acc")

    report = await run_emergency_stop(runtime(sessions, engine, None), BUSINESS)

    assert not report.complete
    assert report.failed == [("acc", "platform_not_connected")]


async def test_business_without_accounts_is_not_reported_as_success(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    report = await run_emergency_stop(runtime(sessions, engine, platform()), uuid.uuid4())
    assert not report.complete
    assert report.failed == [("*", "no_accounts")]


def test_stop_rejects_malformed_business_id(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["emergency-stop", "--business", "не-uuid"])
    assert error.value.code == 2


def test_stop_with_unreachable_database_tells_to_stop_by_hand(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "1")

    code = main(["emergency-stop", "--business", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert code == 1
    assert "вручную" in out
    assert settings.postgres_password.get_secret_value() not in out


async def test_missing_browser_is_a_warning(
    migrated: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Без браузера сервис работает, но сайты на скриптах читает хуже — оператор это видит.
    monkeypatch.setattr(cli, "browser_starts", browser_starts)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert statuses(await run_checks(migrated))["browser"] is CheckStatus.WARN


async def test_auto_mode_is_not_allowed_by_default(migrated: Settings, engine: AsyncEngine) -> None:
    assert Settings().autopilot_auto_allowed is False
    assert statuses(await run_checks(migrated))["autopilot"] is CheckStatus.OK


async def test_allowed_auto_mode_is_a_warning(migrated: Settings, engine: AsyncEngine) -> None:
    allowed = migrated.model_copy(update={"autopilot_auto_allowed": True})
    assert statuses(await run_checks(allowed))["autopilot"] is CheckStatus.WARN


def test_check_reads_the_policy_rules_file() -> None:
    from adhelper.autopilot.adapters.rules_file import load_policy_rules

    assert (load_policy_rules, "policy-1") in cli.RULE_LOADERS


def test_check_reads_the_gate_thresholds_file() -> None:
    from adhelper.autopilot.adapters.rules_file import load_gate_thresholds

    assert (load_gate_thresholds, "gates-1") in cli.RULE_LOADERS
    thresholds = load_gate_thresholds("gates-1")
    # Значения по умолчанию задаёт владелец: пока не подтверждены, вердикта нет.
    assert thresholds.confirmed_by_owner is False


async def test_shadow_report_command_prints_the_week_and_changes_nothing(
    migrated: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli._shadow_report(migrated, 7, "gates-1") == 0
    out = capsys.readouterr().out
    assert "Отчёт теневого периода" in out and "Ворота этапа 9" in out
    assert await cli._shadow_report(migrated, 7, "gates-nope") == 2
