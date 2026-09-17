"""Regenerate the inherited pipeline's outputs for the before/after comparison.

Runs the frozen copy of the inherited code (llm_pipeline.legacy) over every
recipe with a seeded RNG, so its one-random-review-per-recipe selection is
reproducible. Model calls go to gpt-3.5-turbo, as the inherited code does;
the model's own output is not deterministic, so the recipe picks repeat but
the exact edits may differ from run to run. The original unseeded run is
described in docs/ASSESSMENT.md 3.3.

Usage (from repo root):
    uv run python scripts/run_legacy_baseline.py [--seed 4] [--output-dir data/enhanced/baseline]
"""

import argparse
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from llm_pipeline.legacy.pipeline import LLMAnalysisPipeline

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=4)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "enhanced" / "baseline"))
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{level: <7} {message}")

    random.seed(args.seed)
    pipeline = LLMAnalysisPipeline(output_dir=args.output_dir)
    enhanced = pipeline.process_recipe_directory(args.data_dir)
    pipeline.save_summary_report(enhanced)
    print(f"\nseed={args.seed}: {len(enhanced)} enhanced recipe(s) written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
