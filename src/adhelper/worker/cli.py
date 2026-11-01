import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from adhelper.accounts.adapters.crypto import AesGcmCipher, CipherConfigError
from adhelper.accounts.adapters.sql import logins_of_business
from adhelper.autopilot.adapters.rules_file import load_rules as load_autopilot_rules
from adhelper.budget.adapters.rules_file import load_rules as load_budget_rules
from adhelper.budget.application.emergency import StopReport, stop_account
from adhelper.config import Settings
from adhelper.creatives.adapters.factory import build_text_model
from adhelper.creatives.adapters.rules_file import load_rules as load_creative_rules
from adhelper.creatives.adapters.rules_file import load_slot_rules
from adhelper.db import create_engine
from adhelper.experiments.adapters.rules_file import load_rules as load_experiment_rules
from adhelper.worker.demo import (
    ENV_FILE,
    SESSION_FILE,
    NotLocal,
    require_local,
    seed_demo,
    write_demo_env,
)
from adhelper.worker.runtime import Runtime, open_runtime

RULE_LOADERS = (
    (load_budget_rules, "budget-1"),
    (load_autopilot_rules, "autopilot-1"),
    (load_experiment_rules, "experiments-1"),
    (load_creative_rules, "creatives-1"),
    (load_creative_rules, "creatives-2"),
    (load_slot_rules, "ads-1"),
)


# Набор пишется минуты. Час в очереди — воркер не берёт очередь creatives или падает.
STUCK_SETS = text(
    "SELECT count(*) FROM creative_sets"
    " WHERE status IN ('queued', 'running') AND created_at < now() - interval '1 hour'"
)

MANUAL_STOP = "Остановка не завершена: остановите рекламу вручную в кабинете площадки."


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


def _alembic_head() -> str | None:
    for base in (Path.cwd(), Path(__file__).resolve().parents[3]):
        if (base / "alembic.ini").exists():
            config = Config(base / "alembic.ini")
            config.set_main_option("script_location", str(base / "migrations"))
            return ScriptDirectory.from_config(config).get_current_head()
    return None


async def _database_checks(settings: Settings) -> list[CheckResult]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            queue = await connection.scalar(text("SELECT to_regclass('procrastinate_jobs')"))
            stuck = await connection.scalar(STUCK_SETS) if version == _alembic_head() else 0
    except (SQLAlchemyError, OSError) as error:
        # Только тип ошибки: текст может содержать адрес и пароль подключения.
        name = type(error).__name__
        return [CheckResult("database", CheckStatus.FAIL, f"База недоступна или пуста: {name}")]
    finally:
        await engine.dispose()
    head = _alembic_head()
    results = [CheckResult("database", CheckStatus.OK, "База отвечает")]
    if version == head:
        results.append(CheckResult("migrations", CheckStatus.OK, f"Версия {version}"))
    else:
        detail = f"В базе {version}, в коде {head}: запустите миграции"
        results.append(CheckResult("migrations", CheckStatus.FAIL, detail))
    queue_status = CheckStatus.OK if queue else CheckStatus.FAIL
    results.append(CheckResult("queue", queue_status, "Таблицы очереди задач"))
    if stuck:
        detail = (
            f"Наборов объявлений ждут дольше часа: {stuck}. Слушает ли воркер очередь creatives?"
        )
        results.append(CheckResult("ads_writing", CheckStatus.WARN, detail))
    else:
        results.append(CheckResult("ads_writing", CheckStatus.OK, "Тексты объявлений не застряли"))
    return results


def _keys_check(settings: Settings) -> CheckResult:
    keys = settings.token_encryption_keys
    if keys is None or not keys.get_secret_value():
        detail = "TOKEN_ENCRYPTION_KEYS не задан: вход и подключение кабинетов отключены"
        return CheckResult("encryption_keys", CheckStatus.FAIL, detail)
    try:
        AesGcmCipher.from_config(keys.get_secret_value())
    except (CipherConfigError, ValueError):
        # Значение ключа не показываем, даже неверное.
        detail = "TOKEN_ENCRYPTION_KEYS в неверном формате: нужен id:base64 с ключом 32 байта"
        return CheckResult("encryption_keys", CheckStatus.FAIL, detail)
    return CheckResult("encryption_keys", CheckStatus.OK, "Ключи шифрования читаются")


def _url_check(settings: Settings) -> CheckResult:
    if settings.public_base_url.startswith("https://"):
        return CheckResult("public_url", CheckStatus.OK, settings.public_base_url)
    detail = f"{settings.public_base_url}: без HTTPS подходит только для локальной проверки"
    return CheckResult("public_url", CheckStatus.WARN, detail)


def _model_check(name: str, settings: Settings, model_name: str, off: str) -> CheckResult:
    try:
        model = build_text_model(
            settings.text_model_base_url,
            settings.text_model_api_key,
            model_name,
            settings.text_model_project,
        )
    except ValueError as error:
        return CheckResult(name, CheckStatus.FAIL, str(error))
    if model is None:
        return CheckResult(name, CheckStatus.WARN, off)
    host = urlsplit(settings.text_model_base_url).hostname
    return CheckResult(name, CheckStatus.OK, f"{model_name} на {host}")


def _text_model_check(settings: Settings) -> CheckResult:
    off = "Модель не задана: объявления из шаблонов"
    return _model_check("text_model", settings, settings.text_model_name, off)


def _judge_check(settings: Settings) -> CheckResult:
    off = "Судья выключен: смысл объявлений проверяет только код"
    return _model_check("judge", settings, settings.judge_model_name, off)


def _email_check(settings: Settings) -> CheckResult:
    if not settings.smtp_host or not settings.smtp_from:
        detail = "SMTP_HOST или SMTP_FROM не заданы: тревоги на почту ждут в очереди"
        return CheckResult("email", CheckStatus.WARN, detail)
    mode = "STARTTLS" if settings.smtp_starttls else "TLS"
    detail = f"{settings.smtp_host}:{settings.smtp_port}, {mode}, от {settings.smtp_from}"
    return CheckResult("email", CheckStatus.OK, detail)


def _telegram_check(settings: Settings) -> CheckResult:
    token = settings.telegram_bot_token
    if token is None or not token.get_secret_value():
        return CheckResult("telegram", CheckStatus.WARN, "Бот не задан: Telegram не подключить")
    if not settings.telegram_bot_name:
        detail = "TELEGRAM_BOT_NAME не задан: ссылку на бота не собрать"
        return CheckResult("telegram", CheckStatus.FAIL, detail)
    return CheckResult("telegram", CheckStatus.OK, f"@{settings.telegram_bot_name}")


def _rules_check() -> CheckResult:
    try:
        for load, version in RULE_LOADERS:
            load(version)
    except (OSError, ValueError, TypeError) as error:
        return CheckResult("rules", CheckStatus.FAIL, f"Файл правил: {type(error).__name__}")
    return CheckResult("rules", CheckStatus.OK, "Файлы правил читаются")


async def run_checks(settings: Settings) -> list[CheckResult]:
    return [
        *await _database_checks(settings),
        _keys_check(settings),
        _url_check(settings),
        _text_model_check(settings),
        _judge_check(settings),
        _email_check(settings),
        _telegram_check(settings),
        _rules_check(),
    ]


async def run_emergency_stop(runtime: Runtime, business_id: uuid.UUID) -> StopReport:
    report = StopReport()
    logins = await logins_of_business(runtime.sessions, business_id)
    if not logins:
        report.failed.append(("*", "no_accounts"))
    for login in logins:
        platform = runtime.platform_for(business_id, login)
        if platform is None:
            report.failed.append((login, "platform_not_connected"))
            continue
        await stop_account(login, platform, runtime.ledger_for(business_id, login), report)
    return report


def _say(line: str) -> None:
    sys.stdout.write(line + "\n")


def _print_checks(results: Sequence[CheckResult]) -> int:
    for result in results:
        _say(f"[{result.status.value:4}] {result.name}: {result.detail}")
    return 1 if any(result.status is CheckStatus.FAIL for result in results) else 0


async def _emergency_stop(settings: Settings, business_id: uuid.UUID) -> int:
    try:
        async with open_runtime(settings) as runtime:
            report = await run_emergency_stop(runtime, business_id)
    except (SQLAlchemyError, OSError) as error:
        # Только тип ошибки: текст может содержать адрес и пароль подключения.
        _say(f"[НЕ ОСТАНОВЛЕНО] база недоступна: {type(error).__name__}")
        _say(MANUAL_STOP)
        return 1
    for stopped in report.stopped:
        _say(f"[stopped] {stopped}")
    for target, reason in report.failed:
        _say(f"[НЕ ОСТАНОВЛЕНО] {target}: {reason}")
    if report.complete:
        _say("Активных кампаний не осталось.")
        return 0
    _say(MANUAL_STOP)
    return 1


async def _demo_seed(settings: Settings, mismatch: bool) -> int:
    try:
        require_local(settings.public_base_url)
    except NotLocal:
        _say("Демо-данные только для локальной машины: PUBLIC_BASE_URL не localhost.")
        return 1
    root = Path.cwd()
    write_demo_env(root)
    async with open_runtime(settings) as runtime:
        result = await seed_demo(runtime.sessions, runtime.clock(), root, mismatch)
    state = "создан" if result.created else "уже был, выдана новая сессия"
    _say(f"Демо-кабинет {state}. Сессия в {SESSION_FILE}, ключи в {ENV_FILE}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adhelper")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Проверить настройки и базу")
    stop = commands.add_parser("emergency-stop", help="Приостановить всю рекламу бизнеса")
    stop.add_argument("--business", type=uuid.UUID, required=True)
    demo = commands.add_parser("demo-seed", help="Создать демо-кабинет для просмотра интерфейса")
    demo.add_argument(
        "--mismatch", action="store_true", help="Счёт не сходится со статистикой, реклама стоит"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command == "check":
        return _print_checks(asyncio.run(run_checks(settings)))
    if args.command == "demo-seed":
        return asyncio.run(_demo_seed(settings, args.mismatch))
    return asyncio.run(_emergency_stop(settings, args.business))


if __name__ == "__main__":
    sys.exit(main())
