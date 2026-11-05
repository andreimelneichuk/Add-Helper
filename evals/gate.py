"""Ворота выпуска без новой разметки (docs/12-agents.md §2, решение владельца 2026-11-04).

Код детерминирован: снятое им до запуска не доходит. Среди прошедших код выдумка доходит до
запуска, только если она там есть (доля v) и судья её пропустил (доля m). m и долю зря
снятых f меряем один раз на замороженном эталоне (`test`) среди объявлений, которые код не
снял. v оцениваем по доле снятых судьёй b среди прошедших код объявлений нового писателя:
b = v(1 − m) + (1 − v)f (Роган — Гладен). Ошибка среди запущенных —
v·m / (v·m + (1 − v)(1 − f)).

Точную поправку показываем для сведения, а в ворота идёт граница, которая не зависит от f:
из b ≥ v(1 − m) следует v ≤ b / (1 − m). Ложные снятия на эталоне и на текстах нового
писателя бывают разными, и большая f на эталоне иначе занизила бы v до нуля. Границы верхние
для b, m и f (f в ошибке); три границы по 1 − α/3 каждая дают вместе не меньше 1 − α.
Предположение: на текстах нового писателя судья ошибается не чаще, чем на эталоне. Его
проверяет аудитор: расхождения аудитора с судьёй разбирает панель (evals.gate).
"""

import argparse
import asyncio
import dataclasses
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel
from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.application.generate import ADS_SCHEMA, SYSTEM_PROMPT, _parse, build_prompt
from adhelper.creatives.domain.facts import check_claims, keyed_facts
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    ModelUnavailable,
    TextModel,
)
from evals.dataset.build import code_violations, prompt_version, split_of, upper_bound, usable
from evals.dataset.build import load as load_dataset
from evals.dataset.spec import SPLIT_SEED
from evals.meter import (
    DEFAULT_BUDGET_USD,
    FREE,
    MICRO,
    BudgetSpent,
    Meter,
    Price,
    fetch_prices,
    usd,
    worst_case_micro,
)
from evals.prompts import PROMPTS
from evals.run import RESULTS

# Решение владельца: порог доли нарушений в запущенных объявлениях 3%.
LIMIT = 0.03
CONFIDENCE = 0.95


def prevalence(blocked_share: float, miss: float, false_block: float) -> float:
    """Настоящая доля нарушений по видимой доле снятых."""
    sensitivity_gap = 1 - miss - false_block
    if sensitivity_gap <= 0:
        return 1.0
    return min(1.0, max(0.0, (blocked_share - false_block) / sensitivity_gap))


def launched_error(violations: float, miss: float, false_block: float) -> float:
    bad = violations * miss
    good = (1 - violations) * (1 - false_block)
    return bad / (bad + good) if bad + good > 0 else 1.0


@dataclass(frozen=True)
class GateInput:
    # Объявления нового писателя: снято судьёй из прошедших код.
    blocked: int
    written: int
    # Эталон без снятого кодом: пропущено нарушений из нарушений, зря снято чистых из чистых.
    misses: int
    violations: int
    false_blocks: int
    clean: int


@dataclass(frozen=True)
class GateResult:
    violations_share: float
    violations_upper: float
    miss_upper: float
    error: float
    error_upper: float
    passed: bool


def verdict(data: GateInput, limit: float = LIMIT, confidence: float = CONFIDENCE) -> GateResult:
    each = 1 - (1 - confidence) / 3
    b = data.blocked / data.written if data.written else 1.0
    m = data.misses / data.violations if data.violations else 1.0
    f = data.false_blocks / data.clean if data.clean else 0.0
    b_up = upper_bound(data.blocked, data.written, each)
    m_up = upper_bound(data.misses, data.violations, each)
    f_up = upper_bound(data.false_blocks, data.clean, each)
    v = prevalence(b, m, f)
    v_up = min(1.0, b_up / (1 - m_up)) if m_up < 1 else 1.0
    error_up = launched_error(v_up, m_up, f_up)
    return GateResult(v, v_up, m_up, launched_error(v, m, f), error_up, error_up <= limit)


# --- прогон на реальных моделях: тратит деньги, запускается вручную ---
#
#   OPENROUTER_API_KEY=… uv run python -m evals.gate \
#       --writer groq:qwen/qwen3.8-27b --judge or:deepseek/deepseek-v4-flash \
#       --auditor or:z-ai/glm-5.3-flash --budget 0.05
#
# Перед каждым этапом печатается худший случай, в конце — расход; на пределе прогон
# останавливается (docs/13-model-spend.md).
#
# Писатель пишет по тестовым профилям, ответы кешируются в evals/results и в выборку не
# попадают: эталон заморожен. Пропуск и ложные снятия судьи берутся из его замера на эталоне
# (`evals.run judge --dataset test --prompts v4`), объявления нового писателя не размечаются.

OPENROUTER = "https://openrouter.ai/api/v1"
# Сбой провайдера (лимит, 5xx) не оплачен — ждём и повторяем. Оплаченный ответ не по форме
# (не JSON, обрезан) повторяем не больше PAID_RETRIES раз: docs/13-model-spend.md.
ATTEMPTS = 8
PAID_RETRIES = 2
GROQ_PACE_SECONDS = 20.0
# Судье и аудитору набора на объявление, как в evals.run.
JUDGE_MAX_TOKENS = 700


@dataclass(frozen=True)
class _Endpoint:
    model: TextModel
    limited: bool
    gate: asyncio.Semaphore
    meter: Meter
    price: Price = FREE


def _endpoint(spec: str, meter: Meter, prices: dict[str, Price]) -> _Endpoint:
    provider, _, name = spec.partition(":")
    if provider == "groq":
        base, key = os.environ.get("OPENAI_BASE_URL", ""), os.environ.get("OPENAI_API_KEY", "")
    elif provider == "or":
        base, key = OPENROUTER, os.environ.get("OPENROUTER_API_KEY", "")
    else:
        raise SystemExit(f"Модель задаётся как groq:<имя> или or:<имя>, а не {spec}")
    if not base or not key or not name:
        raise SystemExit(f"Нет адреса или ключа для {spec}")
    limited = provider == "groq"
    # Бесплатный уровень Groq денег не берёт: токены считаем, стоимость ноль.
    price = prices.get(name, FREE) if provider == "or" else FREE
    model = OpenAiCompatibleModel(
        base, SecretStr(key), name, on_usage=lambda usage: meter.record(price, usage)
    )
    return _Endpoint(model, limited, asyncio.Semaphore(1 if limited else 8), meter, price)


async def _call(
    endpoint: _Endpoint, system: str, user: str, schema: dict[str, object], max_tokens: int
) -> str | None:
    """Сбой провайдера повторяем, оплаченный негодный ответ — до PAID_RETRIES раз.

    После всех попыток или по исчерпании предела — None.
    """
    paid = 0
    for attempt in range(ATTEMPTS):
        async with endpoint.gate:
            try:
                endpoint.meter.check()
            except BudgetSpent:
                return None
            try:
                reply = await endpoint.model.complete_json(system, user, schema, max_tokens)
                json.loads(reply.text)
            except ModelUnavailable as error:
                wasted = error.tokens > 0
            except ValueError:
                wasted = True
            else:
                if endpoint.limited:
                    await asyncio.sleep(GROQ_PACE_SECONDS)
                return reply.text
        if wasted:
            endpoint.meter.paid_failures += 1
            paid += 1
            if paid > PAID_RETRIES:
                return None
        await asyncio.sleep(min(120, 10 * 2**attempt))
    return None


def _estimate(stage: str, endpoint: _Endpoint, prompts: Sequence[str], max_tokens: int) -> None:
    """Худший случай этапа до запуска: каждый вызов исписывает max_tokens."""
    if not prompts:
        return
    # Кириллица — около 2 символов на токен; берём с запасом.
    input_tokens = max(len(p) for p in prompts) // 2
    worst = worst_case_micro(endpoint.price, len(prompts), input_tokens, max_tokens)
    left = endpoint.meter.budget_micro - endpoint.meter.spent_micro
    note = "" if worst <= left else f", остаток предела {usd(left)}: прогон может остановиться"
    sys.stdout.write(f"{stage}: вызовов {len(prompts)}, не дороже {usd(worst)}{note}\n")


def _slug(spec: str) -> str:
    return spec.replace("/", "_").replace(":", "_")


def _cache(name: str) -> dict[str, list[dict[str, object]]]:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _store(name: str, data: object) -> None:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _drafts(ads: Sequence[dict[str, object]]) -> list[AdDraft]:
    return [AdDraft(str(ad["headline"]), str(ad["text"])) for ad in ads]


async def _write(
    endpoint: _Endpoint,
    profiles: dict[str, BusinessProfile],
    cache: dict[str, Any],
    rules: CreativeRules,
) -> None:
    async def one(pid: str) -> None:
        profile = profiles[pid]
        text = await _call(
            endpoint,
            SYSTEM_PROMPT,
            build_prompt(profile, rules),
            ADS_SCHEMA,
            rules.max_tokens_per_set,
        )
        candidates = _parse(text) if text is not None else None
        if candidates is None:
            return
        facts = keyed_facts(profile)
        ads = []
        for candidate in candidates:
            found = code_violations(candidate.draft, profile, rules)
            # Негодное по форме (длина, пустота) сервис переписывает, в счёт выдумок не идёт.
            if not usable(found):
                continue
            unbound = check_claims(candidate.draft, candidate.claims, facts, rules)
            ads.append(
                {
                    "headline": candidate.draft.headline,
                    "text": candidate.draft.text,
                    "code": sorted({*found, *(v.kind.value for v in unbound)}),
                    "details": [v.detail for v in unbound],
                    "claims": [[c.fragment, c.fact_key] for c in candidate.claims],
                }
            )
        cache[pid] = ads

    todo = [pid for pid in sorted(profiles) if pid not in cache]
    prompts = [SYSTEM_PROMPT + build_prompt(profiles[pid], rules) for pid in todo]
    _estimate("писатель", endpoint, prompts, rules.max_tokens_per_set)
    await asyncio.gather(*(one(pid) for pid in todo))


async def _check(
    endpoint: _Endpoint,
    prompt: str,
    profiles: dict[str, BusinessProfile],
    sets: dict[str, list[dict[str, object]]],
    cache: dict[str, Any],
) -> None:
    """Судья или аудитор набора: одно решение на объявление, сбой — снятие всех."""
    variant = PROMPTS[prompt]

    async def one(pid: str) -> None:
        drafts = _drafts(sets[pid])
        text = await _call(
            endpoint,
            variant.system,
            variant.build(drafts, profiles[pid]),
            variant.schema,
            JUDGE_MAX_TOKENS * len(drafts),
        )
        if text is None:
            return
        cache[pid] = [
            {"blocked": v.blocked, "reason": v.reason} for v in variant.read(text, drafts)
        ]

    todo = [pid for pid in sorted(sets) if sets[pid] and pid not in cache]
    if todo:
        prompts = [
            variant.system + variant.build(_drafts(sets[pid]), profiles[pid]) for pid in todo
        ]
        longest = max(len(sets[pid]) for pid in todo)
        _estimate(prompt, endpoint, prompts, JUDGE_MAX_TOKENS * longest)
    await asyncio.gather(*(one(pid) for pid in todo))


def _reference(judge: str) -> GateInput:
    """Пропуск и ложные снятия судьи на замороженном эталоне среди того, что код не снял."""
    path = RESULTS / f"judge--{_slug(judge.partition(':')[2])}--v4--test-gold.json"
    if not path.exists():
        raise SystemExit(
            f"Нет замера судьи на эталоне: {path.name}. "
            "Сначала evals.run judge --dataset test --prompts v4"
        )
    rows = json.loads(path.read_text(encoding="utf-8"))["cases"]
    passed = [r for r in rows if not r["code_blocked"] and r["label"] != "ambiguous"]
    violations = [r for r in passed if r["label"] == "block"]
    clean = [r for r in passed if r["label"] != "block"]
    return GateInput(
        0,
        0,
        sum(1 for r in violations if not r["votes"][0]),
        len(violations),
        sum(1 for r in clean if r["votes"][0]),
        len(clean),
    )


async def run(args: argparse.Namespace) -> None:
    vendors = {
        spec.partition(":")[2].split("/")[0] for spec in (args.writer, args.judge, args.auditor)
    }
    if len(vendors) < 3:
        raise SystemExit("Писатель, судья и аудитор должны быть от разных разработчиков")
    # На val и train ворот нет: там подгоняем, и печатаются только счётчики снятого.
    reference = _reference(args.judge) if args.split == "test" else None
    rules = load_rules(args.rules)
    data = load_dataset()
    profiles = {
        pid: row.business()
        for pid, row in data.profiles.items()
        if split_of(SPLIT_SEED, pid) == args.split
    }
    # Кеш привязан к промпту и правилам: правка инструкции писателя даёт новый прогон.
    version = prompt_version(SYSTEM_PROMPT, build_prompt(BusinessProfile("", ()), rules))
    writer_name = f"gate--{_slug(args.writer)}--{rules.version}-{version[:8]}--{args.split}"
    written = _cache(writer_name)
    meter = Meter(round(args.budget * MICRO))
    paid = [
        spec.partition(":")[2]
        for spec in (args.writer, args.judge, args.auditor)
        if spec.startswith("or:")
    ]
    prices = fetch_prices(paid) if paid else {}
    for name, price in prices.items():
        sys.stdout.write(
            f"цена {name}: {usd(price.input_micro_per_m)} и {usd(price.output_micro_per_m)}"
            " за 1M входных и выходных\n"
        )
    await _write(_endpoint(args.writer, meter, prices), profiles, written, rules)
    _store(writer_name, written)

    to_judge = {pid: [ad for ad in ads if not ad["code"]] for pid, ads in written.items()}
    judge_name = f"{writer_name}--judge--{_slug(args.judge)}"
    judged = _cache(judge_name)
    await _check(_endpoint(args.judge, meter, prices), "v4", profiles, to_judge, judged)
    _store(judge_name, judged)

    launched = {
        pid: [ad for ad, v in zip(ads, judged.get(pid, []), strict=True) if not v["blocked"]]
        for pid, ads in to_judge.items()
        if pid in judged or not ads
    }
    audit_name = f"{writer_name}--audit--{_slug(args.auditor)}"
    audited = _cache(audit_name)
    await _check(_endpoint(args.auditor, meter, prices), "a1", profiles, launched, audited)
    _store(audit_name, audited)

    report(args, profiles, written, to_judge, judged, launched, audited, reference)
    sys.stdout.write(f"\n{meter.summary()}\n")


def report(
    args: argparse.Namespace,
    profiles: dict[str, BusinessProfile],
    written: dict[str, list[dict[str, Any]]],
    to_judge: dict[str, list[dict[str, Any]]],
    judged: dict[str, list[dict[str, Any]]],
    launched: dict[str, list[dict[str, Any]]],
    audited: dict[str, list[dict[str, Any]]],
    reference: GateInput | None,
) -> None:
    missing = sorted(set(profiles) - set(launched))
    total = sum(len(written[pid]) for pid in launched)
    code_blocked = sum(len(written[pid]) - len(to_judge[pid]) for pid in launched)
    judge_blocked = sum(len(to_judge[pid]) - len(launched[pid]) for pid in launched)
    with_ads = sum(1 for pid in launched if launched[pid])
    out = sys.stdout.write
    out(f"\nписатель {args.writer}, судья {args.judge}, аудитор {args.auditor}\n")
    out(f"правила {args.rules}, выборка {args.split}\n")
    if missing:
        out(f"без ответа {len(missing)} профилей: {', '.join(missing)} — запустите снова\n")
    out(f"объявлений {total}: снял код {code_blocked}, снял судья {judge_blocked}\n")
    out(f"наборов хотя бы с одним объявлением модели: {with_ads} из {len(launched)}\n")
    _codes(written, launched)
    _disagreements(launched, audited)
    _judged(to_judge, judged)
    if reference is None:
        return
    passed = total - code_blocked
    data = GateInput(judge_blocked, passed, *dataclasses.astuple(reference)[2:])
    result = verdict(data)
    out(
        f"эталон: пропущено {data.misses} из {data.violations} нарушений, "
        f"зря снято {data.false_blocks} из {data.clean} чистых\n"
    )
    out(
        f"доля выдумок у писателя {result.violations_share:.1%} "
        f"(до {result.violations_upper:.1%}), "
        f"пропуск до {result.miss_upper:.1%}\n"
    )
    out(
        f"ошибка среди запущенных {result.error:.2%}, верхняя граница {result.error_upper:.2%} "
        f"при пороге {LIMIT:.0%}: {'проходит' if result.passed else 'не проходит'}\n"
    )


def _codes(written: dict[str, list[dict[str, Any]]], launched: dict[str, Any]) -> None:
    reasons = Counter(r for pid in launched for ad in written[pid] for r in ad["code"])
    sys.stdout.write(f"причины кода: {dict(reasons.most_common())}\n")
    details = Counter(
        re.sub(r"«[^»]*»", "«…»", d).split(" из факта")[0].split(" не хватает")[0]
        for pid in launched
        for ad in written[pid]
        for d in ad.get("details", [])
    )
    sys.stdout.write(f"подробности кода: {dict(details.most_common(8))}\n")


def _judged(to_judge: dict[str, list[dict[str, Any]]], judged: dict[str, Any]) -> None:
    sys.stdout.write("снял судья:\n")
    for pid, ads in sorted(to_judge.items()):
        for ad, v in zip(ads, judged.get(pid, []), strict=False):
            if v["blocked"]:
                sys.stdout.write(f"  {pid}: {ad['headline']} | {ad['text']}\n    → {v['reason']}\n")


def _disagreements(launched: dict[str, list[dict[str, Any]]], audited: dict[str, Any]) -> None:
    out = sys.stdout.write
    flagged = [
        (pid, ad, v["reason"])
        for pid, ads in launched.items()
        for ad, v in zip(ads, audited.get(pid, []), strict=True)
        if v["blocked"]
    ]
    unaudited = sum(1 for pid, ads in launched.items() if ads and pid not in audited)
    out(f"аудитор не согласен с судьёй: {len(flagged)} из {sum(map(len, launched.values()))}")
    out(f", без ответа аудитора наборов {unaudited}\n" if unaudited else "\n")
    for pid, ad, reason in flagged:
        out(f"  {pid}: {ad['headline']} | {ad['text']}\n    → {reason}\n")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writer", required=True)
    parser.add_argument("--judge", required=True)
    parser.add_argument("--auditor", required=True)
    parser.add_argument("--rules", default="creatives-3")
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET_USD,
        help="предел прогона в долларах; выше $0.05 — только с согласия владельца",
    )
    asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
