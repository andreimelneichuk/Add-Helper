"""Прогон извлекателя фактов сайта (задача 12): версии правил на одних и тех же страницах.

Платный прогон, правила расхода на модели. Страницы скачиваются и рисуются браузером
один раз, затем каждая версия правил разбирает тот же текст. Печатаются принятые факты и число
отброшенных проверкой цитат: отброшенный факт — это выдумка или неточная цитата модели.

    uv run python -m evals.site --budget 0.02 --rules site-2 site-3 \
        --urls https://taplink.cc/nogti https://nogti.tilda.ws
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from adhelper.business.adapters.browser import ChromiumRenderer
from adhelper.business.adapters.fetcher import SafeFetcher
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.application.site import read_site
from adhelper.business.domain.ports import FetchedPage, PageRenderer, SiteFetcher
from adhelper.config import Settings
from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel
from evals.gate import OPENROUTER, openrouter_key
from evals.meter import MICRO, BudgetSpent, Meter, fetch_prices, usd, worst_case_micro

RESULTS = Path(__file__).parent / "results"
DEFAULT_URLS = ("https://taplink.cc/nogti", "https://nogti.tilda.ws")
# Текст страниц не длиннее max_text_chars, около трёх знаков на токен, с запасом на промпт.
INPUT_TOKENS = 6_000


class CachedFetcher:
    """Скачивание один раз на прогон: версии правил сравниваются на одном тексте."""

    def __init__(self, inner: SiteFetcher) -> None:
        self._inner = inner
        self._pages: dict[str, FetchedPage] = {}

    async def fetch(self, url: str) -> FetchedPage:
        if url not in self._pages:
            self._pages[url] = await self._inner.fetch(url)
        return self._pages[url]


class CachedRenderer:
    def __init__(self, inner: PageRenderer) -> None:
        self._inner = inner
        self._pages: dict[str, FetchedPage] = {}

    async def render(self, url: str) -> FetchedPage:
        if url not in self._pages:
            self._pages[url] = await self._inner.render(url)
        return self._pages[url]


def say(line: str) -> None:
    sys.stdout.write(line + "\n")


async def run(urls: Sequence[str], versions: Sequence[str], budget_usd: float) -> Path:
    key = openrouter_key()
    if not key:
        raise SystemExit("Нет ключа OpenRouter: OPENROUTER_API_KEY или ключ сервиса в .env")
    name = Settings().text_model_name
    price = fetch_prices([name])[name]
    all_rules = [load_site_rules(version) for version in versions]
    calls = len(urls) * len(all_rules)
    max_tokens = max(rules.max_tokens for rules in all_rules)
    worst = worst_case_micro(price, calls, INPUT_TOKENS, max_tokens)
    meter = Meter(round(budget_usd * MICRO))
    say(
        f"Вызовов модели {name}: {calls}; худший случай {usd(worst)}, "
        f"предел {usd(meter.budget_micro)}"
    )
    if worst > meter.budget_micro:
        raise SystemExit("Худший случай выше предела: уменьшите набор или поднимите предел")
    model = OpenAiCompatibleModel(
        OPENROUTER, SecretStr(key), name, on_usage=lambda usage: meter.record(price, usage)
    )
    report: list[dict[str, Any]] = []
    base = all_rules[-1]
    async with ChromiumRenderer(base) as browser:
        fetcher = CachedFetcher(SafeFetcher(base))
        renderer = CachedRenderer(browser)
        for url in urls:
            for rules in all_rules:
                try:
                    meter.check()
                except BudgetSpent:
                    say("Предел исчерпан, прогон остановлен")
                    break
                read = await read_site(url, fetcher, model, rules, renderer)
                say(f"\n[{rules.version}] {url}: {read.status.value} {read.problem or ''}")
                say(f"  страницы: {', '.join(read.pages)}; отброшено: {read.dropped or 'нет'}")
                for fact in read.facts:
                    price_text = f" — {fact.price} ₽" if fact.price is not None else ""
                    say(f"  {fact.field.value}: {fact.value}{price_text}")
                report.append(
                    {
                        "url": url,
                        "rules": rules.version,
                        "status": read.status.value,
                        "problem": read.problem,
                        "pages": list(read.pages),
                        "dropped": dict(read.dropped),
                        "facts": [
                            {
                                "field": f.field.value,
                                "value": f.value,
                                "price": f.price,
                                "quote": f.quote,
                            }
                            for f in read.facts
                        ],
                    }
                )
    out = RESULTS / f"site-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    say("\n" + meter.summary())
    say(f"Отчёт: {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", nargs="+", default=list(DEFAULT_URLS))
    parser.add_argument("--rules", nargs="+", default=["site-2"])
    parser.add_argument("--budget", type=float, default=0.02)
    args = parser.parse_args()
    asyncio.run(run(args.urls, args.rules, args.budget))


if __name__ == "__main__":
    main()
