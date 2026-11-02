"""Прогон оценки на реальной модели. Тратит деньги: запускать вручную, не в CI.

    uv run python -m evals.run judge --models qwen/qwen3-30b-a3b-instruct-2507 --repeats 3
    uv run python -m evals.run generate --models qwen/qwen3-30b-a3b-instruct-2507
    uv run python -m evals.run judge --models openai/gpt-oss-120b --dataset val --labels proposed

`--dataset` берёт выборку из evals/data (см. evals/dataset). Для замера по §2 нужны метки
человека (`--labels human`), предварительные метки генератора годятся только для подгонки.

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
from adhelper.creatives.adapters.factory import build_text_model
from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.application.generate import generate_ads
from adhelper.creatives.domain.checks import check_draft
from adhelper.creatives.domain.model import BusinessProfile, ModelUnavailable, TextModel
from evals.cases import CASES, PROFILES, Case
from evals.dataset.build import load as load_dataset
from evals.dataset.build import to_cases
from evals.prompts import PROMPTS, JudgeVariant
from evals.score import CaseRun, metrics, misses_by_category, single_call_average

RESULTS = Path(__file__).parent / "results"
RULES = load_rules("creatives-2")
PARALLEL = 6


def out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _model(settings: Settings, name: str) -> TextModel:
    model = build_text_model(
        settings.text_model_base_url, settings.text_model_api_key, name, settings.text_model_project
    )
    if model is None:
        raise SystemExit("Нет ключа модели в .env")
    return model


async def _spent(settings: Settings) -> float | None:
    """Расход ключа по данным OpenRouter; у других провайдеров не считаем."""
    if "openrouter.ai" not in settings.text_model_base_url or not settings.text_model_api_key:
        return None
    headers = {"Authorization": f"Bearer {settings.text_model_api_key.get_secret_value()}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
    return float(response.json()["data"]["usage"])


async def _judge_once(
    model: TextModel,
    variant: JudgeVariant,
    case: Case,
    profile: BusinessProfile,
    gate: asyncio.Semaphore,
) -> tuple[bool, str, int]:
    async with gate:
        try:
            reply = await model.complete_json(
                variant.system,
                variant.build(case.draft, profile),
                variant.schema,
                RULES.judge_max_tokens,
            )
        except ModelUnavailable as error:
            return True, f"judge_unavailable:{error}", 0
    verdict = variant.read(reply.text, case.draft)
    return verdict.blocked, verdict.reason, reply.tokens


async def judge_eval(
    settings: Settings,
    model_name: str,
    prompt: str,
    repeats: int,
    cases: Sequence[Case],
    profiles: Mapping[str, BusinessProfile] = PROFILES,
) -> list[CaseRun]:
    model = _model(settings, model_name)
    gate = asyncio.Semaphore(PARALLEL)
    tasks = [
        [
            _judge_once(model, PROMPTS[prompt], case, profiles[case.profile], gate)
            for _ in range(repeats)
        ]
        for case in cases
    ]
    answers = [await asyncio.gather(*row) for row in tasks]
    runs = []
    for case, row in zip(cases, answers, strict=True):
        code = bool(check_draft(case.draft, profiles[case.profile], RULES))
        runs.append(CaseRun(case, tuple(a[0] for a in row), code, tuple(a[1] for a in row)))
    return runs


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
        if not cases:
            raise SystemExit(f"В выборке {part} нет размеченных объявлений")
    else:
        cases = [c for c in CASES if args.split == "all" or c.split == args.split]
        profiles, part = PROFILES, args.split
    before = await _spent(settings)
    for model_name in args.models:
        for prompt in args.prompts:
            runs = await judge_eval(settings, model_name, prompt, args.repeats, cases, profiles)
            title = f"{model_name} · {prompt} · {part} · повторов {args.repeats}"
            summary = report_judge(runs, title)
            _save(
                f"judge--{_slug(model_name)}--{prompt}--{part}",
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
    after = await _spent(settings)
    if before is not None and after is not None:
        out(f"\nпотрачено: ${after - before:.4f}, всего по ключу ${after:.4f} из $1")


async def run_generate(args: argparse.Namespace) -> None:
    settings = Settings()
    before = await _spent(settings)
    for model_name in args.models:
        model = _model(settings, model_name)
        judge = _model(settings, args.judge or model_name)
        rows = []
        for key, profile in PROFILES.items():
            for attempt in range(args.repeats):
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
    after = await _spent(settings)
    if before is not None and after is not None:
        out(f"\nпотрачено: ${after - before:.4f}, всего по ключу ${after:.4f} из $1")


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
    judge.add_argument("--labels", default="human", choices=["human", "proposed"])
    generate = sub.add_parser("generate")
    generate.add_argument("--models", nargs="+", required=True)
    generate.add_argument("--judge", default="")
    generate.add_argument("--repeats", type=int, default=2)
    code = sub.add_parser("code")
    code.add_argument("--rules", nargs="+", default=["creatives-1", "creatives-2"])
    args = parser.parse_args(argv)
    if args.command == "code":
        run_code(args)
        return
    asyncio.run(run_judge(args) if args.command == "judge" else run_generate(args))


if __name__ == "__main__":
    main()
