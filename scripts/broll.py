"""Scripted terminal demo of the pipeline, for recording b-roll.

Runs the commands from the video, one scene at a time: prints a title card,
types the command, runs it, then pauses. Every model response it needs is in
the committed cache, so it runs offline and gives the same output each time.

    uv run python scripts/broll.py                 # all scenes, pause between them
    uv run python scripts/broll.py --scene 4       # one scene, for a retake
    uv run python scripts/broll.py --list
    uv run python scripts/broll.py --fast --no-pause --output-dir /tmp/x   # what the integration test runs

Recording tips: 100 columns, a large font, `clear` is done for you. After
recording, `git checkout data/enhanced` restores the committed outputs
(the run rewrites their created_at stamps).
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUP = "data/enhanced/enhanced_77935_creamy-sweet-potato-with-ginger-soup.json"
COOKIES = "data/enhanced/enhanced_10813_best-chocolate-chip-cookies.json"
BASELINE_COOKIES = "data/enhanced/baseline/enhanced_10813_best-chocolate-chip-cookies.json"

BOLD, DIM, CYAN, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[0m"


@dataclass
class Scene:
    title: str
    caption: str
    command: list[str]
    expect: list[str]  # substrings the integration test asserts on
    shown: str = ""  # what is typed on screen; defaults to the command itself

    def typed(self) -> str:
        return self.shown or shlex.join(self.command)


def scenes(output_dir: str, results_dir: str) -> list[Scene]:
    compare = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "eval" / "results").glob("*.json"))
    order = ["legacy", "gpt-5-nano", "gpt-5-mini", "gpt-4.1-mini"]
    compare.sort(key=lambda p: next((i for i, k in enumerate(order) if k in p), 99))
    return [
        Scene(
            "1  It fails on its own examples",
            "The starter's four few-shot examples, through the starter's own matcher. No API key.",
            ["uv", "run", "python", "scripts/repro_inherited_defects.py"],
            ["Example 4: 2 edits intended, 0 actually applied", "resolves to '2 eggs'", "No API key was used"],
        ),
        Scene(
            "2  What the inherited pipeline wrote",
            "The cookies after the inherited run: both sugars merged into one line, the brown sugar line untouched.",
            ["uv", "run", "python", "scripts/print_ingredients.py", BASELINE_COOKIES],
            ["1 cup white sugar, 1/2 cup packed brown sugar", "1 cup packed brown sugar"],
        ),
        Scene(
            "3  The tests the inherited code failed",
            "D1, D2, D3 pinned as tests before any fix. Now green, with the matcher and composer tests.",
            ["uv", "run", "pytest", "-q", "--ignore=tests/test_integration.py"],
            ["33 passed"],
        ),
        Scene(
            "4  Measure the inherited pipeline",
            "28 labelled cases through the frozen inherited code. Responses come from the committed cache.",
            ["uv", "run", "python", "-m", "eval.run_eval", "--legacy", "--label", "Inherited (gpt-3.5-turbo)", "--results-dir", results_dir],
            ["Modification delivered (in output) | 57%", "False-apply rate (intent / circumstance) | 65%"],
            shown="uv run python -m eval.run_eval --legacy",
        ),
        Scene(
            "5  Measure the rewrite",
            "Same cases, same scorer, the rewritten pipeline on gpt-5-mini.",
            ["uv", "run", "python", "-m", "eval.run_eval", "--label", "Rewrite (gpt-5-mini)", "--results-dir", results_dir],
            ["Modification delivered (in output) | 93%", "Silent no-op rate | 0%"],
            shown="uv run python -m eval.run_eval",
        ),
        Scene(
            "6  Before and after, four configurations",
            "The cheapest model finds everything and applies everything: 100% false-apply.",
            ["uv", "run", "python", "-m", "eval.run_eval", "--compare", *compare],
            ["Inherited (gpt-3.5-turbo)", "Rewrite (gpt-5-nano)", "Rewrite (gpt-5-mini)", "| 100% | 24% | 29% |"],
            shown="uv run python -m eval.run_eval --compare eval/results/*.json",
        ),
        Scene(
            "7  Run every recipe",
            "Every review screened. Three states: enhanced, no tweaks, failed.",
            ["uv", "run", "python", "-m", "llm_pipeline", "run", "--all", "--output-dir", output_dir],
            ["4 enhanced, 3 no tweaks, 0 failed", "recipe has no reviews"],
            shown="uv run python -m llm_pipeline run --all",
        ),
        Scene(
            "8  One line, three opinions",
            "The soup's half-and-half line: applied change, and the two lower-ranked reviewers as alternatives.",
            ["uv", "run", "python", "-m", "llm_pipeline", "show", str(Path(output_dir) / Path(SOUP).name)],
            ["alt (5★): 1.5 cups 2% milk", "alt (4★): 1.5 cups heavy cream", "not applied by the reviewer"],
            shown=f"uv run python -m llm_pipeline show {SOUP}",
        ),
        Scene(
            "9  Eight modifications from one recipe",
            "The cookies: four tweaks from one review, conflicts as alternatives, exclusions with reasons.",
            ["uv", "run", "python", "-m", "llm_pipeline", "show", str(Path(output_dir) / Path(COOKIES).name)],
            ["applied=8", "kept as an alternative", "1 teaspoon cream of tartar"],
            shown=f"uv run python -m llm_pipeline show {COOKIES}",
        ),
    ]


def type_out(text: str, delay: float) -> None:
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        if delay:
            time.sleep(delay)
    sys.stdout.write("\n")


def run_scene(scene: Scene, *, typing: float, pause: float | None, clear: bool, quiet: bool = False) -> tuple[int, str]:
    if clear:
        os.system("clear")
    if not quiet:
        print(f"{BOLD}{CYAN}{scene.title}{RESET}")
        print(f"{DIM}{scene.caption}{RESET}\n")
        time.sleep(0.6 if typing else 0)
        type_out(f"$ {scene.typed()}", typing)
    proc = subprocess.run(scene.command, cwd=ROOT, text=True, capture_output=True)
    output = proc.stdout + proc.stderr
    if not quiet:
        print(output, end="")
    if pause is None:
        input(f"\n{DIM}[enter for next scene]{RESET}")
    elif pause:
        time.sleep(pause)
    return proc.returncode, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", type=int, action="append", help="run only this scene number (repeatable)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--fast", action="store_true", help="no typing animation")
    parser.add_argument("--no-pause", action="store_true", help="do not wait between scenes")
    parser.add_argument("--pause", type=float, help="seconds between scenes instead of waiting for enter")
    parser.add_argument("--no-clear", action="store_true")
    parser.add_argument("--output-dir", default="data/enhanced")
    parser.add_argument("--results-dir", default="eval/results")
    args = parser.parse_args(argv)

    all_scenes = scenes(args.output_dir, args.results_dir)
    if args.list:
        for i, s in enumerate(all_scenes, 1):
            print(f"{i}  {s.title[3:]:<40} {s.typed()[:70]}")
        return 0
    chosen = [all_scenes[i - 1] for i in args.scene] if args.scene else all_scenes
    pause = 0.0 if args.no_pause else args.pause
    failures = 0
    for scene in chosen:
        code, _ = run_scene(scene, typing=0 if args.fast else 0.03, pause=pause, clear=not args.no_clear)
        if code != 0:
            failures += 1
            print(f"{BOLD}scene exited with {code}{RESET}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
