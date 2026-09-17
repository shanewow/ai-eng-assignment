"""Adapters that run one (recipe, review) through a pipeline and report edit outcomes.

`LegacyAdapter` reproduces the inherited extractor call exactly (same prompt
builder, same model, same json_object mode, temperature 0.1, max_tokens 1000)
and applies the result through the frozen inherited modifier, recording what
each edit actually did. It makes one attempt per review; the inherited code
retried an identical prompt on parse errors, which the cache would answer
identically anyway.

`PipelineAdapter` runs the rewritten extractor and modifier. Each modification
is applied independently against the original recipe, because this eval
measures extraction and application per review; ranking and conflict
resolution across reviews are covered by tests/test_composer.py.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from llm_pipeline.llm_client import CachedChatClient

from .scoring import CaseRun, EditOutcome


class Adapter(Protocol):
    name: str
    model: str

    def run(self, case_id: str, recipe_data: dict, review: dict) -> CaseRun: ...


def _ref(target: str, index: int | None) -> str | None:
    if index is None:
        return None
    return f"{'I' if target == 'ingredients' else 'S'}{index}"


class LegacyAdapter:
    name = "legacy"

    def __init__(self, client: CachedChatClient, model: str = "gpt-3.5-turbo"):
        from llm_pipeline.legacy.recipe_modifier import RecipeModifier

        self.client = client
        self.model = model
        self.modifier = RecipeModifier()

    def run(self, case_id: str, recipe_data: dict, review: dict) -> CaseRun:
        from llm_pipeline.legacy.models import ModificationObject, Recipe
        from llm_pipeline.legacy.prompts import build_simple_prompt

        recipe = Recipe(
            recipe_id=recipe_data["recipe_id"],
            title=recipe_data["title"],
            ingredients=recipe_data["ingredients"],
            instructions=recipe_data["instructions"],
        )
        prompt = build_simple_prompt(
            review["text"], recipe.title, recipe.ingredients, recipe.instructions
        )
        run = CaseRun(case_id=case_id)
        try:
            result = self.client.complete(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1000,
            )
            run.usage, run.cached, run.raw = result.usage, result.cached, result.content
            modification = ModificationObject(**json.loads(result.content))
        except Exception as exc:  # parse or validation error, or transport
            run.error = f"{type(exc).__name__}: {exc}"
            return run

        run.mods_returned = 1
        working = {
            "ingredients": list(recipe.ingredients),
            "instructions": list(recipe.instructions),
        }
        original = {
            "ingredients": list(recipe.ingredients),
            "instructions": list(recipe.instructions),
        }
        for edit in modification.edits:
            content = working[edit.target]
            _, index, _ = self.modifier.find_best_match(edit.find, content)
            line_ref = None
            if index is not None:
                text = content[index]
                orig_index = original[edit.target].index(text) if text in original[edit.target] else index
                line_ref = _ref(edit.target, orig_index)
            new_content, records = self.modifier.apply_edit(edit, content)
            working[edit.target] = new_content
            if not records:
                status, from_text, to_text = "failed", "", ""
            elif records[0].from_text == records[0].to_text:
                status, from_text, to_text = "noop", records[0].from_text, records[0].to_text
            else:
                status, from_text, to_text = "applied", records[0].from_text, records[0].to_text
            run.outcomes.append(
                EditOutcome(
                    target=edit.target,
                    operation=edit.operation,
                    find=edit.find,
                    new_text=edit.replace or edit.add or "",
                    line_ref=line_ref,
                    status=status,
                    intended=True,
                    from_text=from_text,
                    to_text=to_text,
                )
            )
        return run


class PipelineAdapter:
    name = "pipeline"

    def __init__(self, client: CachedChatClient, model: str):
        from llm_pipeline.recipe_modifier import RecipeModifier
        from llm_pipeline.tweak_extractor import TweakExtractor

        self.client = client
        self.model = model
        self.extractor = TweakExtractor(client=client, model=model)
        self.modifier = RecipeModifier()

    def run(self, case_id: str, recipe_data: dict, review: dict) -> CaseRun:
        from llm_pipeline.models import Review
        from llm_pipeline.pipeline import parse_recipe

        recipe = parse_recipe(recipe_data)
        rev = Review(
            text=review["text"],
            rating=review.get("rating"),
            is_featured=bool(review.get("is_featured")),
        )
        run = CaseRun(case_id=case_id)
        try:
            extraction = self.extractor.extract(rev, recipe)
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            return run
        run.usage, run.cached, run.raw = extraction.usage, extraction.cached, extraction.raw
        run.mods_returned = len(extraction.modifications)

        for mi, mod in enumerate(extraction.modifications):
            intended = mod.was_applied_by_reviewer and mod.is_generalizable
            _, results = self.modifier.apply_modification_detailed(recipe, mod)
            for r in results:
                run.outcomes.append(
                    EditOutcome(
                        target=r.edit.target,
                        operation=r.edit.operation,
                        find=r.edit.find,
                        new_text=r.edit.replace or r.edit.add or "",
                        line_ref=_ref(r.edit.target, r.line_index),
                        status=r.status if intended else "excluded",
                        intended=intended,
                        mod_index=mi,
                        from_text=r.from_text,
                        to_text=r.to_text,
                        reason=r.reason,
                    )
                )
        return run


def make_adapter(kind: str, client: CachedChatClient, model: str | None) -> Any:
    if kind == "legacy":
        return LegacyAdapter(client, model or "gpt-3.5-turbo")
    from llm_pipeline.llm_client import DEFAULT_MODEL

    return PipelineAdapter(client, model or DEFAULT_MODEL)
