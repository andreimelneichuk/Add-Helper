"""Ворота выпуска без новой разметки (docs/12-agents.md §2, решение владельца 2026-11-04).

Выдумка доходит до запуска, только если писатель её написал (доля v) и код с судьёй её
пропустили (доля m). m и долю зря снятых f меряем один раз на замороженном эталоне (`test`).
v у нового писателя оцениваем по доле снятых b среди его объявлений с поправкой на известные
ошибки проверки (Роган — Гладен): b = v(1 − m) + (1 − v)f. Ошибка среди запущенных —
v·m / (v·m + (1 − v)(1 − f)).

Границы берём худшие в своём направлении: верхние для b и m, нижнюю для f в оценке v и
верхнюю для f в ошибке. Три границы по 1 − α/3 каждая дают вместе не меньше 1 − α.
Предположение: на текстах нового писателя судья ошибается не чаще, чем на эталоне. Его
проверяет аудитор: расхождения аудитора с судьёй разбирает панель (evals.gate).
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel
from adhelper.creatives.application.generate import ADS_SCHEMA, SYSTEM_PROMPT, _parse, build_prompt
from adhelper.creatives.domain.facts import check_claims, keyed_facts
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, ModelUnavailable, TextModel
from evals.dataset.authored import RULES
from evals.dataset.build import code_violations, split_of, upper_bound, usable
from evals.dataset.build import load as load_dataset
from evals.dataset.spec import SPLIT_SEED
from evals.prompts import PROMPTS
from evals.run import RESULTS

LIMIT = 0.01
CONFIDENCE = 0.95


def lower_bound(errors: int, total: int, confidence: float = CONFIDENCE) -> float:
    if total == 0 or errors == 0:
        return 0.0
    return 1 - upper_bound(total - errors, total, confidence)


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
    # Объявления нового писателя: снято кодом или судьёй из всех пригодных.
    blocked: int
    written: int
    # Эталон: пропущено нарушений из всех нарушений, зря снято чистых из всех чистых.
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
    f_lo = lower_bound(data.false_blocks, data.clean, each)
    f_up = upper_bound(data.false_blocks, data.clean, each)
    v = prevalence(b, m, f)
    v_up = prevalence(b_up, m_up, f_lo)
    error_up = launched_error(v_up, m_up, f_up)
    return GateResult(v, v_up, m_up, launched_error(v, m, f), error_up, error_up <= limit)


# --- прогон на реальных моделях: тратит деньги, запускается вручную ---
#
#   OPENROUTER_API_KEY=… uv run python -m evals.gate \
#       --writer groq:qwen/qwen3.8-27b --judge or:deepseek/deepseek-v4-flash \
#       --auditor or:z-ai/glm-5.3-flash
#
# Писатель пишет по тестовым профилям, ответы кешируются в evals/results и в выборку не
# попадают: эталон заморожен. Пропуск и ложные снятия судьи берутся из его замера на эталоне
# (`evals.run judge --dataset test --prompts v4`), объявления нового писателя не размечаются.

OPENROUTER = "https://openrouter.ai/api/v1"
ATTEMPTS = 8
GROQ_PACE_SECONDS = 20.0


@dataclass(frozen=True)
class _Endpoint:
    model: TextModel
    limited: bool
    gate: asyncio.Semaphore


def _endpoint(spec: str) -> _Endpoint:
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
    model = OpenAiCompatibleModel(base, SecretStr(key), name)
    return _Endpoint(model, limited, asyncio.Semaphore(1 if limited else 8))


async def _call(
    endpoint: _Endpoint, system: str, user: str, schema: dict[str, object], max_tokens: int
) -> str | None:
    """Сбой провайдера и не-JSON повторяем; после всех попыток — None."""
    for attempt in range(ATTEMPTS):
        async with endpoint.gate:
            try:
                reply = await endpoint.model.complete_json(system, user, schema, max_tokens)
                json.loads(reply.text)
            except (ModelUnavailable, ValueError):
                pass
            else:
                if endpoint.limited:
                    await asyncio.sleep(GROQ_PACE_SECONDS)
                return reply.text
        await asyncio.sleep(min(120, 10 * 2**attempt))
    return None


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
    endpoint: _Endpoint, profiles: dict[str, BusinessProfile], cache: dict[str, Any]
) -> None:
    async def one(pid: str) -> None:
        profile = profiles[pid]
        text = await _call(
            endpoint,
            SYSTEM_PROMPT,
            build_prompt(profile, RULES),
            ADS_SCHEMA,
            RULES.max_tokens_per_set,
        )
        candidates = _parse(text) if text is not None else None
        if candidates is None:
            return
        facts = keyed_facts(profile)
        ads = []
        for candidate in candidates:
            found = code_violations(candidate.draft, profile, RULES)
            # Негодное по форме (длина, пустота) сервис переписывает, в счёт выдумок не идёт.
            if not usable(found):
                continue
            unbound = check_claims(candidate.draft, candidate.claims, facts)
            ads.append(
                {
                    "headline": candidate.draft.headline,
                    "text": candidate.draft.text,
                    "code": sorted({*found, *(v.kind.value for v in unbound)}),
                }
            )
        cache[pid] = ads

    await asyncio.gather(*(one(pid) for pid in sorted(profiles) if pid not in cache))


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
            RULES.judge_max_tokens * len(drafts),
        )
        if text is None:
            return
        cache[pid] = [
            {"blocked": v.blocked, "reason": v.reason} for v in variant.read(text, drafts)
        ]

    await asyncio.gather(*(one(pid) for pid in sorted(sets) if sets[pid] and pid not in cache))


def _reference(judge: str) -> GateInput:
    """Пропуск и ложные снятия кода с судьёй на замороженном эталоне."""
    path = RESULTS / f"judge--{_slug(judge.partition(':')[2])}--v4--test-gold.json"
    if not path.exists():
        raise SystemExit(
            f"Нет замера судьи на эталоне: {path.name}. "
            "Сначала evals.run judge --dataset test --prompts v4"
        )
    combined = json.loads(path.read_text(encoding="utf-8"))["summary"]["combined"]
    return GateInput(
        0,
        0,
        combined["violations"] - combined["caught"],
        combined["violations"],
        combined["false_blocks"],
        combined["clean"],
    )


async def run(args: argparse.Namespace) -> None:
    vendors = {
        spec.partition(":")[2].split("/")[0] for spec in (args.writer, args.judge, args.auditor)
    }
    if len(vendors) < 3:
        raise SystemExit("Писатель, судья и аудитор должны быть от разных разработчиков")
    reference = _reference(args.judge)
    data = load_dataset()
    profiles = {
        pid: row.business()
        for pid, row in data.profiles.items()
        if split_of(SPLIT_SEED, pid) == "test"
    }
    writer_name = f"gate--{_slug(args.writer)}"
    written = _cache(writer_name)
    await _write(_endpoint(args.writer), profiles, written)
    _store(writer_name, written)

    to_judge = {pid: [ad for ad in ads if not ad["code"]] for pid, ads in written.items()}
    judge_name = f"{writer_name}--judge--{_slug(args.judge)}"
    judged = _cache(judge_name)
    await _check(_endpoint(args.judge), "v4", profiles, to_judge, judged)
    _store(judge_name, judged)

    launched = {
        pid: [ad for ad, v in zip(ads, judged.get(pid, []), strict=True) if not v["blocked"]]
        for pid, ads in to_judge.items()
        if pid in judged or not ads
    }
    audit_name = f"{writer_name}--audit--{_slug(args.auditor)}"
    audited = _cache(audit_name)
    await _check(_endpoint(args.auditor), "a1", profiles, launched, audited)
    _store(audit_name, audited)

    report(args, profiles, written, to_judge, judged, launched, audited, reference)


def report(
    args: argparse.Namespace,
    profiles: dict[str, BusinessProfile],
    written: dict[str, list[dict[str, Any]]],
    to_judge: dict[str, list[dict[str, Any]]],
    judged: dict[str, list[dict[str, Any]]],
    launched: dict[str, list[dict[str, Any]]],
    audited: dict[str, list[dict[str, Any]]],
    reference: GateInput,
) -> None:
    missing = sorted(set(profiles) - set(launched))
    total = sum(len(written[pid]) for pid in launched)
    code_blocked = sum(len(written[pid]) - len(to_judge[pid]) for pid in launched)
    judge_blocked = sum(len(to_judge[pid]) - len(launched[pid]) for pid in launched)
    data = GateInput(code_blocked + judge_blocked, total, *dataclasses.astuple(reference)[2:])
    result = verdict(data)
    with_ads = sum(1 for pid in launched if launched[pid])
    out = sys.stdout.write
    out(f"\nписатель {args.writer}, судья {args.judge}, аудитор {args.auditor}\n")
    if missing:
        out(f"без ответа {len(missing)} профилей: {', '.join(missing)} — запустите снова\n")
    out(f"объявлений {total}: снял код {code_blocked}, снял судья {judge_blocked}\n")
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
    out(f"наборов хотя бы с одним объявлением модели: {with_ads} из {len(launched)}\n")
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
    asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
