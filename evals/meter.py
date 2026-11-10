"""Учёт денег платных прогонов evals (правила расхода на платные модели).

Деньги в целых микродолларах. Стоимость ответа берём у провайдера (OpenRouter отдаёт её в
`usage.cost`), иначе считаем по цене из каталога и округляем вверх. Прогон останавливается,
когда расход дошёл до предела: вызовы, уже начатые параллельно, ещё доходят.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

import httpx

from adhelper.creatives.adapters.openai_compatible import Usage

MICRO = 1_000_000
CATALOG = "https://openrouter.ai/api/v1/models"
# Предел прогона по умолчанию: выше — только после согласия владельца.
DEFAULT_BUDGET_USD = 0.05
# Решение владельца: модели не дороже $1.50 за 1M выходных токенов.
MAX_OUTPUT_MICRO_PER_M = 1_500_000


class BudgetSpent(Exception):
    """Предел прогона исчерпан."""


@dataclass(frozen=True)
class Price:
    """Цена в микродолларах за миллион токенов."""

    input_micro_per_m: int
    output_micro_per_m: int


FREE = Price(0, 0)


def cost_micro(price: Price, prompt_tokens: int, completion_tokens: int) -> int:
    total = prompt_tokens * price.input_micro_per_m + completion_tokens * price.output_micro_per_m
    return math.ceil(total / 1_000_000)


def worst_case_micro(price: Price, calls: int, input_tokens: int, max_tokens: int) -> int:
    """Худший случай: каждый вызов исписывает max_tokens."""
    return calls * cost_micro(price, input_tokens, max_tokens)


def usd(micro: int) -> str:
    return f"${micro / MICRO:.4f}"


@dataclass
class Meter:
    budget_micro: int
    spent_micro: int = 0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    paid_failures: int = 0
    stopped: bool = False

    def check(self) -> None:
        if self.spent_micro >= self.budget_micro:
            self.stopped = True
            raise BudgetSpent

    def record(self, price: Price, usage: Usage) -> None:
        self.calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        if usage.cost_usd is not None:
            self.spent_micro += math.ceil(Decimal(str(usage.cost_usd)) * MICRO)
        else:
            self.spent_micro += cost_micro(price, usage.prompt_tokens, usage.completion_tokens)

    def summary(self) -> str:
        line = (
            f"расход {usd(self.spent_micro)} из предела {usd(self.budget_micro)}: "
            f"ответов {self.calls}, токенов на входе {self.prompt_tokens}, "
            f"на выходе {self.completion_tokens}, оплаченных сбоев {self.paid_failures}"
        )
        return line + (" — ОСТАНОВЛЕНО ПО ПРЕДЕЛУ" if self.stopped else "")


def _per_m(value: object) -> int:
    # Каталог даёт доллары за токен строкой: Decimal, чтобы 0.00000025 не стало 250 001.
    return math.ceil(Decimal(str(value)) * MICRO * 1_000_000)


def parse_prices(payload: Mapping[str, object], models: Iterable[str]) -> dict[str, Price]:
    rows = payload.get("data")
    catalog = {
        str(row["id"]): row["pricing"]
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(row.get("pricing"), dict)
    }
    prices = {}
    for name in models:
        if name not in catalog:
            raise SystemExit(f"Нет цены модели {name} в каталоге OpenRouter: прогон не начат")
        pricing = catalog[name]
        price = Price(_per_m(pricing["prompt"]), _per_m(pricing["completion"]))
        if price.output_micro_per_m > MAX_OUTPUT_MICRO_PER_M:
            raise SystemExit(
                f"{name} дороже предела: {usd(price.output_micro_per_m)} за 1M выходных, "
                f"можно до {usd(MAX_OUTPUT_MICRO_PER_M)}"
            )
        prices[name] = price
    return prices


def fetch_prices(models: Iterable[str]) -> dict[str, Price]:
    """Каталог OpenRouter открыт без ключа и денег не тратит."""
    headers = {"User-Agent": "AdHelper/1.0 (evals price meter)"}
    response = httpx.get(CATALOG, headers=headers, timeout=30)
    response.raise_for_status()
    return parse_prices(response.json(), models)
