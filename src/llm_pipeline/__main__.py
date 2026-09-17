"""Command line entry point.

    uv run python -m llm_pipeline run --recipe data/recipe_10813_best-chocolate-chip-cookies.json
    uv run python -m llm_pipeline run --all [--data-dir data] [--output-dir data/enhanced]
    uv run python -m llm_pipeline show data/enhanced/enhanced_10813_best-chocolate-chip-cookies.json

Exit status is non-zero only when a recipe genuinely failed. A recipe with no
applicable community tweaks is reported as no_tweaks, not as a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from .pipeline import LLMAnalysisPipeline


def cmd_run(args: argparse.Namespace) -> int:
    pipeline = LLMAnalysisPipeline(output_dir=args.output_dir, model=args.model, use_cache=not args.no_cache)
    if args.all:
        results = pipeline.process_recipe_directory(args.data_dir)
    else:
        results = [pipeline.process_single_recipe(args.recipe)]
    report = pipeline.summarize(results)
    if args.all:
        pipeline.save_summary_report(results)

    print()
    for row in report["recipes"]:
        line = f"{row['status']:<10} {row['recipe_file']}"
        if row["status"] == "enhanced":
            line += f"  applied={row['modifications_applied']} considered={row['modifications_considered']} line_changes={row['line_changes']}"
        elif row["status"] == "no_tweaks":
            line += f"  ({row['reason']})"
        else:
            line += f"  {row.get('error', '')}"
        print(line)
    c = report["pipeline_summary"]
    print(f"\n{c['recipes']} recipes: {c['enhanced']} enhanced, {c['no_tweaks']} no tweaks, {c['failed']} failed")
    return 1 if c["failed"] else 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print an enhanced recipe as a line diff a person can read."""
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    print(data["title"])
    s = data["enhancement_summary"]
    print(f"status={s['status']} applied={s['modifications_applied']} considered={s['modifications_considered']} line_changes={s['total_changes']}\n")
    for mod in data["modifications_applied"]:
        r = mod["source_review"]
        star = "featured, " if r.get("is_featured") else ""
        print(f"APPLIED  [{mod['modification_type']}] {mod['summary']}   ({star}{r.get('rating')}★)")
        for ch in mod["changes_made"]:
            ref = f"{'I' if ch['type'] == 'ingredient' else 'S'}{ch['line_index']}"
            if ch["operation"] == "add":
                print(f"    + after {ref}: {ch['to_text']}")
            elif ch["operation"] == "remove":
                print(f"    - {ref}: {ch['from_text']}")
            else:
                print(f"    - {ref}: {ch['from_text']}\n    + {ref}: {ch['to_text']}")
            for alt in ch.get("alternatives", []):
                a = alt["source_review"]
                print(f"      alt ({a.get('rating')}★): {alt['proposed_text']}   [{alt['summary']}]")
        for f in mod.get("edits_failed", []):
            print(f"    ! not applied: {f}")
    for mod in data.get("modifications_considered", []):
        r = mod["source_review"]
        print(f"SKIPPED  [{mod['modification_type']}] {mod['summary']}   ({r.get('rating')}★)\n    reason: {mod['reason']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm_pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="enhance one recipe or every recipe in a directory")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--recipe", help="path to one recipe JSON")
    target.add_argument("--all", action="store_true", help="every recipe_*.json in --data-dir")
    run.add_argument("--data-dir", default="data")
    run.add_argument("--output-dir", default="data/enhanced")
    run.add_argument("--model", help="OpenAI model (default: OPENAI_MODEL or gpt-5-nano)")
    run.add_argument("--no-cache", action="store_true", help="bypass the extraction cache")
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show", help="print an enhanced recipe as a readable diff")
    show.add_argument("file")
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.verbose else "INFO", format="<level>{level: <7}</level> {message}")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
