"""Reproduce the inherited pipeline's apply-layer defects with no API key.

Feeds the starter's OWN few-shot prompt examples -- the ones it shows the model
as the definition of correct output -- through the starter's OWN modifier, and
reports what actually happens.

Usage (from repo root):  uv run python scripts/repro_inherited_defects.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

from llm_pipeline.legacy.models import ModificationEdit, ModificationObject, Recipe  # noqa: E402
from llm_pipeline.legacy.prompts import FEW_SHOT_EXAMPLES  # noqa: E402
from llm_pipeline.legacy.recipe_modifier import RecipeModifier  # noqa: E402

logger.remove()  # the modifier logs at INFO; we want only our own report

RECIPE_PATH = ROOT / "data" / "recipe_10813_best-chocolate-chip-cookies.json"


def load_cookie_recipe() -> Recipe:
    data = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    return Recipe(
        recipe_id=data["recipe_id"],
        title=data["title"],
        ingredients=data["ingredients"],
        instructions=data["instructions"],
    )


def check_own_examples(recipe: Recipe, modifier: RecipeModifier) -> None:
    print("=" * 78)
    print("A. The starter's own few-shot examples, run through the starter's own modifier")
    print("=" * 78)

    for i, example in enumerate(FEW_SHOT_EXAMPLES, start=1):
        modification = ModificationObject(**example["expected_output"])
        _, changes = modifier.apply_modification(recipe, modification)
        real = [c for c in changes if c.from_text != c.to_text]

        verdict = "OK" if len(real) == len(modification.edits) else "BROKEN"
        print(
            f"\nExample {i}: {len(modification.edits)} edits intended, "
            f"{len(real)} actually applied   [{verdict}]"
        )
        for edit in modification.edits:
            target = recipe.ingredients if edit.target == "ingredients" else recipe.instructions
            _, _, score = modifier.find_best_match(edit.find, target)
            print(f"    {edit.operation:<9} find={edit.find!r:<45} best similarity {score:.2f}")


def check_silent_noop(recipe: Recipe, modifier: RecipeModifier) -> None:
    print("\n" + "=" * 78)
    print("B. A change record is emitted for an edit that changed nothing")
    print("=" * 78)

    # A reviewer writes "1/2 teaspoon"; the recipe says "0.5 teaspoon".
    modification = ModificationObject(
        modification_type="quantity_adjustment",
        reasoning="Reviewer doubled the salt",
        edits=[
            ModificationEdit(
                target="ingredients",
                operation="replace",
                find="1/2 teaspoon salt",
                replace="1 teaspoon salt",
            )
        ],
    )
    modified, changes = modifier.apply_modification(recipe, modification)
    match, index, score = modifier.find_best_match("1/2 teaspoon salt", recipe.ingredients)

    print(f"\n    fuzzy match       : {match!r} at similarity {score:.2f}")
    print(f"    change reported   : {changes[0].from_text!r} -> {changes[0].to_text!r}")
    print(f"    ingredient after  : {modified.ingredients[index]!r}")
    print(f"    changed?          : {changes[0].from_text != changes[0].to_text}")


def check_ambiguity(recipe: Recipe, modifier: RecipeModifier) -> None:
    print("\n" + "=" * 78)
    print("C. Short strings match the wrong line")
    print("=" * 78 + "\n")

    for probe in ("1 egg", "1 cup sugar", "salt"):
        match, _, score = modifier.find_best_match(probe, recipe.ingredients)
        print(f"    find={probe!r:<15} resolves to {match!r} at similarity {score:.2f}")


def main() -> None:
    recipe = load_cookie_recipe()
    modifier = RecipeModifier()

    check_own_examples(recipe, modifier)
    check_silent_noop(recipe, modifier)
    check_ambiguity(recipe, modifier)

    print("\n" + "=" * 78)
    print("No API key was used. Every result above comes from the inherited code.")
    print("=" * 78)


if __name__ == "__main__":
    main()
