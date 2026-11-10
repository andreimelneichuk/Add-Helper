"""Шутаут генераторов картинок (задача 12): одни и те же сцены, несколько моделей.

Платный прогон, правила расхода на модели. Перед стартом печатается число вызовов и
худший случай по ценам OpenRouter; на пределе `--budget` прогон останавливается.

    uv run python -m evals.images --budget 0.32 \
        --models black-forest-labs/flux.2-pro recraft/recraft-v4.1 \
                 black-forest-labs/flux.2-klein-4b recraft/recraft-v4.1-flash

Сцены 2 и 3 пишет писатель сервиса (`write_scene`) и проверяет код, сцена 1 задана руками:
крупный план рук с маникюром нужен, чтобы проверить, рисуют ли модели руки без артефактов
(правило images-1 такие сцены для ногтевого сервиса запрещает). Картинки, миниатюры 150×150
и отчёт складываются в evals/results/images-<время>/ — в репозиторий не попадают.
"""

import argparse
import asyncio
import io
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from pydantic import SecretStr

from adhelper.config import Settings
from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel
from adhelper.creatives.adapters.openrouter_images import OpenRouterImages
from adhelper.creatives.adapters.photos import prepare_image
from adhelper.creatives.adapters.rules_file import load_image_rules
from adhelper.creatives.application.images import write_scene
from adhelper.creatives.domain.images import ImageRejected, ImageUnavailable, final_prompt
from evals.gate import OPENROUTER, openrouter_key
from evals.meter import MICRO, BudgetSpent, Meter, fetch_prices, usd

RESULTS = Path(__file__).parent / "results"
DEFAULT_MODELS = (
    "black-forest-labs/flux.2-pro",
    "recraft/recraft-v4.1",
    "black-forest-labs/flux.2-klein-4b",
    "recraft/recraft-v4.1-flash",
)
# Квадрат 1:1 у FLUX — 1024×1024, то есть чуть больше мегапикселя: цену берём за 1,1 Мп.
MEGAPIXELS_1_1 = Decimal("1.1")
# Писатель фото-запросов: за запрос не больше этого (цена каталога × max_tokens), с запасом.
WRITER_WORST_MICRO = 1_000


@dataclass(frozen=True)
class Scenario:
    key: str
    niche: str
    headline: str
    # Готовая сцена без писателя: для проверки рук.
    scene: str = ""


SCENARIOS = (
    Scenario(
        "nails",
        "маникюр",
        "Нюдовый маникюр",
        "Close-up of a woman's hands with a fresh nude gel manicure resting on a white towel, "
        "professional soft studio light, clean minimalist nail studio, 85mm macro lens",
    ),
    Scenario("yoga", "студия аэройоги", "Аэройога утром у панорамных окон"),
    Scenario("auto", "автосервис", "Диагностика автомобиля в чистом боксе"),
)


def image_price_micro(endpoint: dict[str, Any]) -> int:
    """Цена одной квадратной картинки по `pricing` эндпоинта OpenRouter, вверх до микродоллара."""
    total = Decimal(0)
    for item in endpoint.get("pricing") or []:
        if item.get("billable") not in ("output_image", None):
            continue
        cost = Decimal(str(item["cost_usd"]))
        total += cost * MEGAPIXELS_1_1 if item.get("unit") == "megapixel" else cost
    if total == 0:
        raise SystemExit(f"Нет цены картинки у {endpoint.get('provider_name')}: прогон не начат")
    return math.ceil(total * MICRO)


def fetch_image_prices(models: tuple[str, ...]) -> dict[str, int]:
    prices = {}
    for model in models:
        response = httpx.get(f"{OPENROUTER}/images/models/{model}/endpoints", timeout=30)
        response.raise_for_status()
        payload = response.json()
        # Ответ без обёртки `data`: {"id": ..., "endpoints": [...]}, сверено 2026-11-05.
        endpoints = payload.get("data", payload)["endpoints"]
        prices[model] = max(image_price_micro(endpoint) for endpoint in endpoints)
    return prices


def say(line: str) -> None:
    sys.stdout.write(line + "\n")


def thumbnail(data: bytes, side: int = 150) -> bytes:
    """Миниатюра как в поиске Яндекса: по ней смотрим читаемость сцены."""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((side, side))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def run(models: tuple[str, ...], budget_usd: float) -> Path:
    key = openrouter_key()
    if not key:
        raise SystemExit("Нет ключа OpenRouter: OPENROUTER_API_KEY или ключ сервиса в .env")
    rules = load_image_rules("images-1")
    writer_name = Settings().text_model_name
    prices = fetch_image_prices(models)
    writer_price = fetch_prices([writer_name])[writer_name]
    writer_calls = sum(1 for s in SCENARIOS if not s.scene)
    worst = sum(prices.values()) * len(SCENARIOS) + writer_calls * WRITER_WORST_MICRO
    say(
        f"Вызовов: {len(models) * len(SCENARIOS)} картинок и {writer_calls} фото-запросов; "
        f"худший случай {usd(worst)}, предел {usd(round(budget_usd * MICRO))}"
    )
    for model, price in prices.items():
        say(f"  {model}: {usd(price)} за картинку")
    meter = Meter(round(budget_usd * MICRO))
    if worst > meter.budget_micro:
        raise SystemExit("Худший случай выше предела: уменьшите набор моделей или поднимите предел")

    out = RESULTS / f"images-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    writer = OpenAiCompatibleModel(
        OPENROUTER,
        SecretStr(key),
        writer_name,
        on_usage=lambda usage: meter.record(writer_price, usage),
    )
    report: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        problems: tuple[str, ...] = ()
        if scenario.scene:
            prompt, source = final_prompt(scenario.scene, rules), "manual"
        else:
            meter.check()
            scene = await write_scene(writer, scenario.niche, "", scenario.headline, rules)
            prompt, source, problems = scene.text, scene.source, scene.problems
        say(f"\n[{scenario.key}] запрос ({source}): {prompt}")
        for model in models:
            try:
                meter.check()
            except BudgetSpent:
                say("Предел исчерпан, прогон остановлен")
                break
            spent: list[int | None] = []
            generator = OpenRouterImages(OPENROUTER, SecretStr(key), model, on_cost=spent.append)
            started = time.monotonic()
            row: dict[str, Any] = {"scenario": scenario.key, "model": model, "prompt": prompt}
            row.update({"prompt_source": source, "prompt_problems": list(problems)})
            try:
                made = await generator.generate(prompt, rules.aspect_ratio)
                prepared = prepare_image(made.data, rules)
                name = f"{scenario.key}--{model.replace('/', '_')}"
                (out / f"{name}.jpg").write_bytes(prepared.data)
                (out / f"{name}--150.png").write_bytes(thumbnail(prepared.data))
                row.update(
                    {
                        "file": f"{name}.jpg",
                        "media_type": made.media_type,
                        "size": [prepared.facts.width, prepared.facts.height],
                    }
                )
            except (ImageUnavailable, ImageRejected) as error:
                row["error"] = f"{type(error).__name__}: {error}"
            row["seconds"] = round(time.monotonic() - started, 1)
            cost = next((c for c in spent if c is not None), None)
            if spent and cost is None:
                # Провайдер не сообщил цену: считаем по каталогу.
                cost = prices[model]
            meter.spent_micro += cost or 0
            row["cost_usd"] = (cost or 0) / MICRO
            report.append(row)
            result = row.get("error") or row["file"]
            say(f"  {model}: {result}, {row['seconds']} с, {usd(cost or 0)}")
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    say("\n" + meter.summary())
    say(f"Картинки: {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--budget", type=float, default=0.05)
    args = parser.parse_args()
    asyncio.run(run(tuple(args.models), args.budget))


if __name__ == "__main__":
    main()
