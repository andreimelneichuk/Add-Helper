"""Прогон оценки на реальной модели. Тратит деньги: запускать вручную, не в CI.

    uv run python -m evals.run judge --models qwen/qwen3-30b-a3b-instruct-2507 --repeats 3
    uv run python -m evals.run generate --models qwen/qwen3-30b-a3b-instruct-2507
    uv run python -m evals.run judge --models openai/gpt-oss-120b --dataset val

`--dataset` берёт выборку из evals/dataset. Для замера по §2 — `--labels gold`: метка человека
или единогласие панели разметчиков; `author` — метка автора, только для подгонки на train.

Адрес и ключ берутся из .env (TEXT_MODEL_BASE_URL, TEXT_MODEL_API_KEY, TEXT_MODEL_PROJECT).
Результаты пишутся в evals/results/ как JSON.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

import httpx

from adhelper.config import Settings
from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel
from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.application.generate import generate_ads
from adhelper.creatives.domain.checks import check_draft
from adhelper.creatives.domain.model import BusinessProfile, ModelUnavailable, TextModel
from evals.cases import CASES, PROFILES, Case
from evals.dataset.build import load as load_dataset
from evals.dataset.build import to_cases
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
from evals.prompts import PROMPTS, JudgeVariant
from evals.score import CaseRun, metrics, misses_by_category, single_call_average

RESULTS = Path(__file__).parent / "results"
RULES = load_rules("creatives-2")
# Бесплатный уровень Groq: 8000 токенов в минуту, а запрос резервирует и max_tokens:
# два вызова судьи набора сразу уже упираются в лимит.
PARALLEL = 1
ATTEMPTS = 8
# Оплаченный негодный ответ (обрезан, пуст) повторяем не больше двух раз.
PAID_RETRIES = 2
# Пауза после вызова внутри очереди: около 2000 токенов на вызов, три вызова в минуту.
PACE_SECONDS = 20.0


def out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _model(settings: Settings, name: str, meter: Meter) -> tuple[TextModel, Price]:
    """Модель с учётом расхода: цена из каталога OpenRouter, у других провайдеров ноль."""
    key = settings.text_model_api_key
    if key is None or not key.get_secret_value() or not name:
        raise SystemExit("Нет ключа модели в .env")
    paid = "openrouter.ai" in settings.text_model_base_url
    price = fetch_prices([name])[name] if paid else FREE
    out(f"цена {name}: {usd(price.input_micro_per_m)} и {usd(price.output_micro_per_m)} за 1M")
    model = OpenAiCompatibleModel(
        settings.text_model_base_url,
        key,
        name,
        settings.text_model_project,
        on_usage=lambda usage: meter.record(price, usage),
    )
    return model, price


async def _spent(settings: Settings) -> tuple[float, str] | None:
    """Расход и предел ключа по данным OpenRouter; у других провайдеров не считаем."""
    if "openrouter.ai" not in settings.text_model_base_url or not settings.text_model_api_key:
        return None
    headers = {"Authorization": f"Bearer {settings.text_model_api_key.get_secret_value()}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
    data = response.json()["data"]
    limit = data.get("limit")
    return float(data["usage"]), "без предела" if limit is None else f"${float(limit):.2f}"


async def _judge_once(
    model: TextModel,
    variant: JudgeVariant,
    cases: Sequence[Case],
    profile: BusinessProfile,
    gate: asyncio.Semaphore,
    meter: Meter,
    pace: float = 0.0,
) -> list[tuple[bool, str, int]]:
    """Один вызов на объявление или, для судьи набора, на все объявления профиля."""
    drafts = [case.draft for case in cases]
    # Лимит провайдера — не ответ судьи: ждём и повторяем, а в замер идёт последний сбой.
    paid = 0
    failure: Exception = ModelUnavailable("no_attempts")
    for attempt in range(ATTEMPTS):
        async with gate:
            try:
                meter.check()
            except BudgetSpent:
                return [(True, "judge_unavailable:budget", 0) for _ in cases]
            try:
                reply = await model.complete_json(
                    variant.system,
                    variant.build(drafts, profile),
                    variant.schema,
                    RULES.judge_max_tokens * (len(drafts) if variant.per_set else 1),
                )
            except ModelUnavailable as error:
                failure = error
            else:
                if pace:
                    await asyncio.sleep(pace)
                break
        if isinstance(failure, ModelUnavailable) and failure.tokens:
            meter.paid_failures += 1
            paid += 1
            if paid > PAID_RETRIES:
                return [(True, f"judge_unavailable:{failure}", 0) for _ in cases]
        await asyncio.sleep(min(120, 10 * 2**attempt))
    else:
        return [(True, f"judge_unavailable:{failure}", 0) for _ in cases]
    verdicts = variant.read(reply.text, drafts)
    return [(v.blocked, v.reason, reply.tokens // len(cases)) for v in verdicts]


async def judge_eval(
    settings: Settings,
    model_name: str,
    prompt: str,
    repeats: int,
    cases: Sequence[Case],
    profiles: Mapping[str, BusinessProfile] = PROFILES,
    previous: Mapping[str, CaseRun] | None = None,
    meter: Meter | None = None,
) -> list[CaseRun]:
    """previous — прошлый прогон того же замера: готовые ответы не запрашиваем снова."""
    meter = meter or Meter(round(DEFAULT_BUDGET_USD * MICRO))
    model, price = _model(settings, model_name, meter)
    variant = PROMPTS[prompt]
    # Лимиты в минуту есть у бесплатного Groq; у OpenRouter платим за токены и не ждём.
    limited = "groq.com" in settings.text_model_base_url
    gate = asyncio.Semaphore(PARALLEL if limited else 8)
    pace = PACE_SECONDS if limited and variant.per_set else 0.0
    groups: list[list[Case]] = []
    if variant.per_set:
        by_profile: dict[str, list[Case]] = {}
        for case in cases:
            by_profile.setdefault(case.profile, []).append(case)
        groups = list(by_profile.values())
    else:
        groups = [[case] for case in cases]
    done = previous or {}
    todo = [g for g in groups if not all(_answered(done.get(c.id), repeats) for c in g)]
    if todo:
        longest = max(len(g) for g in todo) if variant.per_set else 1
        prompt_chars = max(
            len(variant.system + variant.build([c.draft for c in g], profiles[g[0].profile]))
            for g in todo
        )
        worst = worst_case_micro(
            price, len(todo) * repeats, prompt_chars // 2, RULES.judge_max_tokens * longest
        )
        out(f"{model_name} · {prompt}: вызовов {len(todo) * repeats}, не дороже {usd(worst)}")
    answers = await asyncio.gather(
        *(
            asyncio.gather(
                *(
                    _judge_once(
                        model, variant, group, profiles[group[0].profile], gate, meter, pace
                    )
                    for _ in range(repeats)
                )
            )
            for group in todo
        )
    )
    tokens = sum(a[2] for row in answers for repeat in row for a in repeat)
    out(f"токенов судьи: {tokens}, наборов запрошено {len(todo)} из {len(groups)}")
    fresh = {id(group): row for group, row in zip(todo, answers, strict=True)}
    runs: list[CaseRun] = []
    for group in groups:
        if id(group) not in fresh:
            runs.extend(done[case.id] for case in group)
            continue
        row = fresh[id(group)]
        for position, case in enumerate(group):
            code = bool(check_draft(case.draft, profiles[case.profile], RULES))
            votes = tuple(repeat[position][0] for repeat in row)
            reasons = tuple(repeat[position][1] for repeat in row)
            runs.append(CaseRun(case, votes, code, reasons))
    return runs


def sample_sets(cases: Sequence[Case], size: int) -> list[Case]:
    """Малая выборка для отсева: наборы целиком,
    сначала с нарушениями, по порядку профилей, пока не наберётся size объявлений; 0 — все."""
    if not size:
        return list(cases)
    by_profile: dict[str, list[Case]] = {}
    for case in cases:
        by_profile.setdefault(case.profile, []).append(case)
    order = sorted(by_profile, key=lambda p: (all(c.label != "block" for c in by_profile[p]), p))
    picked: list[Case] = []
    for profile in order:
        if len(picked) >= size:
            break
        picked.extend(by_profile[profile])
    return picked


def _answered(run: CaseRun | None, repeats: int) -> bool:
    return (
        run is not None
        and len(run.votes) == repeats
        and not any(r.startswith("judge_unavailable") for r in run.reasons)
    )


def _previous(name: str) -> dict[str, CaseRun]:
    path = RESULTS / f"{name}.json"
    if not path.exists():
        return {}
    saved = json.loads(path.read_text(encoding="utf-8"))["cases"]
    fields = {f for f in Case.__dataclass_fields__}
    return {
        row["id"]: CaseRun(
            Case(**{k: v for k, v in row.items() if k in fields}),
            tuple(row["votes"]),
            row["code_blocked"],
            tuple(row["reasons"]),
        )
        for row in saved
    }


def report_judge(runs: Sequence[CaseRun], title: str) -> dict[str, object]:
    recall, false_block = single_call_average(runs)
    summary: dict[str, object] = {
        "title": title,
        "single_recall": recall,
        "single_false_block": false_block,
    }
    out(f"\n== {title}")
    out(f"одиночный вызов: ловит {recall:.0f}% нарушений, снимает зря {false_block:.0f}% чистых")
    for verdict in ("majority", "code", "combined", "combined_majority"):
        m = metrics(runs, verdict)
        summary[verdict] = asdict(m) | {
            "recall_pct": m.recall_pct,
            "false_block_pct": m.false_block_pct,
        }
        out(
            f"{verdict:18} ловит {m.caught}/{m.violations} ({m.recall_pct:.0f}%), "
            f"зря снимает {m.false_blocks}/{m.clean} ({m.false_block_pct:.0f}%)"
        )
        if verdict in ("majority", "combined"):
            out(f"   пропущены: {', '.join(m.missed_ids) or '—'}")
            out(f"   сняты зря: {', '.join(m.false_block_ids) or '—'}")
    unstable = [run.case.id for run in runs if run.unstable]
    summary["unstable"] = unstable
    summary["misses_by_category"] = dict(misses_by_category(runs, "majority"))
    out(f"разные ответы на повторах: {', '.join(unstable) or '—'}")
    return summary


async def run_judge(args: argparse.Namespace) -> None:
    settings = Settings()
    if args.dataset:
        cases, profiles = to_cases(load_dataset(), args.dataset, args.labels)
        part = f"{args.dataset}-{args.labels}"
        if args.writer:
            # Только объявления писателя одной версии: w — до AI2, f — со ссылками на факты.
            cases = [
                c
                for c in cases
                if c.category == "writer" and c.id.rsplit("-", 1)[1].startswith(args.writer)
            ]
            part += f"-{args.writer}"
        if args.sample:
            cases = sample_sets(cases, args.sample)
            part += f"-s{args.sample}"
        if not cases:
            raise SystemExit(f"В выборке {part} нет размеченных объявлений")
    else:
        cases = [c for c in CASES if args.split == "all" or c.split == args.split]
        profiles, part = PROFILES, args.split
    meter = Meter(round(args.budget * MICRO))
    before = await _spent(settings)
    for model_name in args.models:
        for prompt in args.prompts:
            name = f"judge--{_slug(model_name)}--{prompt}--{part}"
            previous = _previous(name) if args.resume else {}
            runs = await judge_eval(
                settings, model_name, prompt, args.repeats, cases, profiles, previous, meter
            )
            title = f"{model_name} · {prompt} · {part} · повторов {args.repeats}"
            summary = report_judge(runs, title)
            _save(
                name,
                {
                    "summary": summary,
                    "cases": [
                        asdict(run.case)
                        | {
                            "votes": run.votes,
                            "reasons": run.reasons,
                            "code_blocked": run.code_blocked,
                        }
                        for run in runs
                    ],
                },
            )
    out(f"\n{meter.summary()}")
    after = await _spent(settings)
    if before is not None and after is not None:
        out(f"по ключу: за прогон ${after[0] - before[0]:.4f}, всего ${after[0]:.4f} из {after[1]}")


async def run_generate(args: argparse.Namespace) -> None:
    settings = Settings()
    meter = Meter(round(args.budget * MICRO))
    before = await _spent(settings)
    for model_name in args.models:
        model, _ = _model(settings, model_name, meter)
        judge, _ = _model(settings, args.judge or model_name, meter)
        rows = []
        for key, profile in PROFILES.items():
            for attempt in range(args.repeats):
                if meter.spent_micro >= meter.budget_micro:
                    meter.stopped = True
                    break
                result = await generate_ads(profile, model, RULES, judge)
                rows.append(
                    {
                        "profile": key,
                        "attempt": attempt,
                        "source": result.source.value,
                        "tokens": result.tokens_used,
                        "notes": list(result.notes),
                        "kept": [asdict(d) for d in result.drafts],
                        "rejected": [
                            asdict(r.draft)
                            | {
                                "violations": [
                                    {"kind": v.kind.value, "detail": v.detail} for v in r.violations
                                ]
                            }
                            for r in result.rejected
                        ],
                    }
                )
                out(
                    f"{model_name} {key}#{attempt}: {result.source.value}, "
                    f"оставлено {len(result.drafts)}, снято {len(result.rejected)}, "
                    f"токенов {result.tokens_used}, {list(result.notes)}"
                )
        _save(
            f"generate--{_slug(model_name)}",
            {"model": model_name, "judge": args.judge or model_name, "rows": rows},
        )
    out(f"\n{meter.summary()}")
    after = await _spent(settings)
    if before is not None and after is not None:
        out(f"по ключу: за прогон ${after[0] - before[0]:.4f}, всего ${after[0]:.4f} из {after[1]}")


def _slug(name: str) -> str:
    return name.replace("/", "_")


def _save(name: str, data: object) -> None:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    out(f"записано: {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


def run_code(args: argparse.Namespace) -> None:
    """Только детерминированная проверка: без сети и без денег."""
    for version in args.rules:
        rules = load_rules(version)
        runs = [
            CaseRun(c, (False,), bool(check_draft(c.draft, PROFILES[c.profile], rules)))
            for c in CASES
        ]
        m = metrics(runs, "code")
        out(
            f"{version}: ловит {m.caught}/{m.violations} ({m.recall_pct:.0f}%), "
            f"зря снимает {m.false_blocks}/{m.clean} ({m.false_block_pct:.0f}%)"
        )
        out(f"   сняты зря: {', '.join(m.false_block_ids) or '—'}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="evals.run")
    sub = parser.add_subparsers(dest="command", required=True)
    judge = sub.add_parser("judge")
    judge.add_argument("--models", nargs="+", required=True)
    judge.add_argument("--prompts", nargs="+", default=["v1"], choices=sorted(PROMPTS))
    judge.add_argument("--repeats", type=int, default=3)
    judge.add_argument("--split", default="all", choices=["all", "dev", "holdout", "fresh"])
    judge.add_argument("--dataset", default="", choices=["", "train", "val", "test", "all"])
    judge.add_argument("--labels", default="gold", choices=["gold", "human", "author"])
    judge.add_argument("--writer", default="", choices=["", "w", "f"])
    judge.add_argument("--resume", action="store_true", help="доспросить только сбои")
    judge.add_argument("--sample", type=int, default=0, help="около N объявлений для отсева")
    generate = sub.add_parser("generate")
    generate.add_argument("--models", nargs="+", required=True)
    generate.add_argument("--judge", default="")
    generate.add_argument("--repeats", type=int, default=2)
    for command in (judge, generate):
        command.add_argument(
            "--budget",
            type=float,
            default=DEFAULT_BUDGET_USD,
            help="предел прогона в долларах; выше $0.05 — только с согласия владельца",
        )
    code = sub.add_parser("code")
    code.add_argument("--rules", nargs="+", default=["creatives-1", "creatives-2"])
    args = parser.parse_args(argv)
    if args.command == "code":
        run_code(args)
        return
    asyncio.run(run_judge(args) if args.command == "judge" else run_generate(args))


if __name__ == "__main__":
    main()
