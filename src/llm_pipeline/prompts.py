"""Prompt and output schema for modification extraction.

Layout matters for cost: the recipe (identical across all of a recipe's
reviews) comes first and the review last, so provider-side prompt caching can
hit on the shared prefix. The version string is stamped into every output so
a prompt change can be re-run selectively.
"""

from __future__ import annotations

from typing import Optional

from .models import Recipe, Review

PROMPT_VERSION = "2.1"

MODIFICATION_TYPES = [
    "ingredient_substitution",
    "quantity_adjustment",
    "technique_change",
    "addition",
    "removal",
]

SYSTEM_PROMPT = """You are a careful recipe editor. You read one community review of a recipe and extract every discrete modification the reviewer describes, as precise edits to the recipe's numbered lines.

Rules
1. One modification per discrete change, each with exactly one category. "I added an egg and halved the sugar" is two modifications.
2. Report every modification you find, then set two flags honestly:
   - was_applied_by_reviewer: true only if the reviewer actually made the change. "Next time I will", "I would prefer", "it would probably also work" are false.
   - is_generalizable: true only if the change would help another cook making this recipe. False when the reviewer did it only because of what they happened to have or lack ("because that's what I had", "I was out of X so", "only because I had some left over"), did it by accident, reverted it, or reports the result was worse. A change made out of circumstance is not generalizable even if the result was fine.
3. Edits reference the numbered lines (I3 = ingredient line 3, S6 = instruction step 6). `find` must be text copied verbatim from that line: a fragment, or the whole line. Do not paraphrase it.
4. Changing the amount or form of an existing ingredient is a `replace` on that ingredient's line, never an `add_after`. The replacement keeps a quantity and unit ("1 tablespoon fresh grated ginger", not "fresh grated ginger"). If the reviewer gives no amount, keep the original amount and append ", or more to taste".
5. A new ingredient is an `add_after` with the full new line in `add`, anchored by `line_ref` to a neighbouring ingredient line. One `add_after` adds exactly one line; two new ingredients are two edits. If a step should mention it, also replace that step's text.
6. Removing an ingredient is a `remove` of its ingredient line, plus a `replace` on any step that names it so the step no longer mentions it.
7. Technique changes (temperature, time, chilling, pressing, portioning, order) are a `replace` on the instruction step, keeping the step readable.
8. Never invent a change. Remarks that are not changes to the recipe (portion size, that three bananas equal the stated cups, general praise) are not modifications. A reviewer who followed the recipe as written has no modifications: return an empty list. Never write commentary about the reviewer into recipe text; every `replace` and `add` is text a cook would follow.
9. Unused string fields are "". `replace` is used only by replace edits, `add` only by add_after edits. Never put a line id such as "I11:" inside `find`, `replace` or `add`; those fields hold recipe text only.

Example. Recipe lines include "I7: 0.5 teaspoon salt", "I10: 1 cup chopped walnuts", "S4: Stir in flour, chocolate chips, and walnuts.", "S5: Drop spoonfuls of dough 2 inches apart onto ungreased baking sheets."
Review: "I used 1 tsp of salt instead of 1/2 and left out the nuts. Pressed them flat before baking, which helped. Next time I'll try brown butter."
Output:
{"modifications": [
 {"summary": "salt 1/2 tsp -> 1 tsp", "modification_type": "quantity_adjustment", "reasoning": "Reviewer found the cookies bland with the original amount", "was_applied_by_reviewer": true, "is_generalizable": true,
  "edits": [{"target": "ingredients", "operation": "replace", "line_ref": "I7", "find": "0.5 teaspoon salt", "replace": "1 teaspoon salt", "add": ""}]},
 {"summary": "omit the walnuts", "modification_type": "removal", "reasoning": "Reviewer preferred them without nuts", "was_applied_by_reviewer": true, "is_generalizable": true,
  "edits": [{"target": "ingredients", "operation": "remove", "line_ref": "I10", "find": "1 cup chopped walnuts", "replace": "", "add": ""},
            {"target": "instructions", "operation": "replace", "line_ref": "S4", "find": "Stir in flour, chocolate chips, and walnuts.", "replace": "Stir in flour and chocolate chips.", "add": ""}]},
 {"summary": "press dough flat before baking", "modification_type": "technique_change", "reasoning": "Gives a more even, less domed cookie", "was_applied_by_reviewer": true, "is_generalizable": true,
  "edits": [{"target": "instructions", "operation": "replace", "line_ref": "S5", "find": "Drop spoonfuls of dough 2 inches apart onto ungreased baking sheets.", "replace": "Drop spoonfuls of dough 2 inches apart onto ungreased baking sheets and press each one down slightly.", "add": ""}]},
 {"summary": "try brown butter next time", "modification_type": "ingredient_substitution", "reasoning": "Stated as a future idea, not tried", "was_applied_by_reviewer": false, "is_generalizable": true,
  "edits": []}
]}"""


def format_recipe(recipe: Recipe) -> str:
    lines = [f"Recipe: {recipe.title}"]
    if recipe.servings:
        lines.append(f"Servings: {recipe.servings}")
    lines.append("")
    lines.append("Ingredients:")
    lines += [f"I{i}: {text}" for i, text in enumerate(recipe.ingredients)]
    lines.append("")
    lines.append("Instructions:")
    lines += [f"S{i}: {text}" for i, text in enumerate(recipe.instructions)]
    return "\n".join(lines)


def build_user_prompt(review: Review, recipe: Recipe) -> str:
    rating = f"{review.rating}/5" if review.rating is not None else "unrated"
    featured = ", listed as a featured tweak" if review.is_featured else ""
    return (
        f"{format_recipe(recipe)}\n\n"
        f"Review (rating {rating}{featured}):\n"
        f'"""{review.text.strip()}"""\n\n'
        "Extract every discrete modification in this review as JSON."
    )


def build_messages(review: Review, recipe: Recipe) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(review, recipe)},
    ]


_EDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "target": {"type": "string", "enum": ["ingredients", "instructions"]},
        "operation": {"type": "string", "enum": ["replace", "add_after", "remove"]},
        "line_ref": {"type": "string", "description": "I<n> or S<n>"},
        "find": {"type": "string", "description": "verbatim text from the referenced line; '' for the whole line"},
        "replace": {"type": "string"},
        "add": {"type": "string"},
    },
    "required": ["target", "operation", "line_ref", "find", "replace", "add"],
}

_MODIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "modification_type": {"type": "string", "enum": MODIFICATION_TYPES},
        "reasoning": {"type": "string"},
        "was_applied_by_reviewer": {"type": "boolean"},
        "is_generalizable": {"type": "boolean"},
        "edits": {"type": "array", "items": _EDIT_SCHEMA},
    },
    "required": ["summary", "modification_type", "reasoning", "was_applied_by_reviewer", "is_generalizable", "edits"],
}

EXTRACTION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "extraction_result",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"modifications": {"type": "array", "items": _MODIFICATION_SCHEMA}},
            "required": ["modifications"],
        },
    },
}


def retry_messages(messages: list[dict[str, str]], raw: str, error: str) -> list[dict[str, str]]:
    """Feed a validation failure back so the retry is not an identical request."""
    return messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": f"That output failed validation: {error}\nReturn corrected JSON only."},
    ]


def describe(review: Review, limit: int = 80) -> Optional[str]:
    text = review.text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
