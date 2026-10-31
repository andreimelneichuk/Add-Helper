from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def platform_date(moment: datetime) -> date:
    """Сутки площадки: Директ считает их по московскому времени."""
    return moment.astimezone(MOSCOW).date()


def moscow_period(start: date, days: int) -> tuple[datetime, datetime]:
    """Границы бюджетного периода в UTC: от полуночи до полуночи по Москве."""
    starts_at = datetime.combine(start, time.min, tzinfo=MOSCOW).astimezone(UTC)
    ends_at = datetime.combine(start + timedelta(days=days), time.min, tzinfo=MOSCOW)
    return starts_at, ends_at.astimezone(UTC)
