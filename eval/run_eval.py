"""Run the labelled cases through a pipeline and score it.

    uv run python -m eval.run_eval --legacy                 # inherited code, gpt-3.5-turbo
    uv run python -m eval.run_eval                          # rewritten code, default model
    uv run python -m eval.run_eval --model gpt-4.1-mini
    uv run python -m eval.run_eval --compare eval/results/a.json eval/results/b.json

Responses are cached under data/cache/llm by request hash, so a second run
of the same configuration makes no API calls. Results land in eval/results.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from llm_pipeline.llm_client import CachedChatClient

from .adapters import make_adapter
from .scoring import aggregate, outcome_dict, render_table, score_case

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "cases.json"
RESULTS = ROOT / "eval" / "results"
DATA = ROOT / "data"


def load_cases(path: Path, ids: list[str] | None) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    return cases


def resolve_review(case: dict, recipe_data: dict) -> dict:
    if "review_index" in case:
        review = dict(recipe_data["reviews"][case["review_index"]])
        featured = {t["text"] for t in recipe_data.get("featured_tweaks") or []}
        review["is_featured"] = review["text"] in featured
        return review
    return dict(case["review"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy", action="store_true", help="score the frozen inherited pipeline")
    parser.add_argument("--model", help="OpenAI model (default: gpt-3.5-turbo for --legacy, else OPENAI_MODEL or gpt-5-nano)")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--ids", nargs="*", help="run only these case ids")
    parser.add_argument("--no-cache", action="store_true", help="bypass the response cache")
    parser.add_argument("--label", help="column label for the table (default: adapter/model)")
    parser.add_argument("--compare", nargs="+", type=Path, help="render a table from saved result files and exit")
    parser.add_argument("--verbose", "-v", action="store_true", help="print every edit outcome")
    parser.add_argument("--results-dir", type=Path, default=RESULTS, help="where to write the result JSON")
    args = parser.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.verbose else "WARNING")
    load_dotenv(ROOT / ".env")

    if args.compare:
        columns = []
        for p in args.compare:
            saved = json.loads(p.read_text(encoding="utf-8"))
            columns.append((saved["label"], saved["aggregate"]))
        print(render_table(columns))
        return 0

    client = CachedChatClient(cache_dir=DATA / "cache" / "llm", use_cache=not args.no_cache)
    adapter = make_adapter("legacy" if args.legacy else "pipeline", client, args.model)
    label = args.label or f"{adapter.name} / {adapter.model}"

    cases = load_cases(args.cases, args.ids)
    recipes: dict[str, dict] = {}
    scores, runs, per_case = [], [], []
    for case in cases:
        recipe_data = recipes.setdefault(
            case["recipe"], json.loads((DATA / case["recipe"]).read_text(encoding="utf-8"))
        )
        review = resolve_review(case, recipe_data)
        run = adapter.run(case["id"], recipe_data, review)
        score = score_case(case, run, recipe_data["ingredients"])
        scores.append(score)
        runs.append(run)
        per_case.append({"score": asdict(score), "run": {**asdict(run), "outcomes": [outcome_dict(o) for o in run.outcomes]}})

        flag = "ERR " if run.error else "    "
        print(
            f"{flag}{case['id']:<36} mods={run.mods_returned:<2} "
            f"recall={score.found_must_apply}/{score.expected_must_apply} delivered={score.delivered_must_apply} "
            f"false_apply={score.false_applied}/{score.expected_must_not} "
            f"applied={score.applied_edits}/{score.intended_edits} "
            f"noop={score.noop_records} wrong_line={score.wrong_line_edits}"
            + (" [cached]" if run.cached else "")
        )
        if run.error:
            print(f"      {run.error}")
        if args.verbose:
            for o in run.outcomes:
                print(f"      {o.status:<9} {o.operation:<9} {o.line_ref or '-':<4} find={o.find!r} -> {o.new_text!r} {o.reason}")

    agg = aggregate(scores, runs)
    print()
    print(render_table([(label, agg)]))
    print(f"\ntokens: {agg['tokens']}")

    args.results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.results_dir / f"{adapter.name}-{adapter.model}-{stamp}.json"
    out.write_text(
        json.dumps(
            {"label": label, "adapter": adapter.name, "model": adapter.model, "cases_file": str(args.cases.relative_to(ROOT)) if args.cases.is_relative_to(ROOT) else str(args.cases), "created_at": stamp, "aggregate": agg, "cases": per_case},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
