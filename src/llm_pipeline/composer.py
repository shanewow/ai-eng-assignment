"""Composition: turn many per-review extractions into one enhanced recipe.

No model call. Modifications are ranked (featured first, then rating, then
recency), applied in that order, and two modifications that target the same
original line are a conflict: the higher-ranked one is applied and the other
is attached to that line as an alternative with its source review.

Line identity is tracked by original index, so conflict detection is exact
even after earlier edits have inserted or removed lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from loguru import logger

from .models import (
    Alternative,
    ChangeRecord,
    EnhancementSummary,
    ExtractionResult,
    ModificationApplied,
    ModificationConsidered,
    ModificationObject,
    Recipe,
    Review,
    SourceReview,
)
from .recipe_modifier import RecipeModifier, locate


def rank_key(review: Review) -> Tuple[bool, int, int]:
    """Featured first, then higher rating, then more recent (lower index)."""
    return (not review.is_featured, -(review.rating or 0), review.index)


def source_of(review: Review) -> SourceReview:
    return SourceReview(text=review.text, reviewer=review.username, rating=review.rating, is_featured=review.is_featured)


@dataclass
class _Line:
    orig_index: Optional[int]
    text: str


@dataclass
class Composition:
    ingredients: List[str]
    instructions: List[str]
    applied: List[ModificationApplied] = field(default_factory=list)
    considered: List[ModificationConsidered] = field(default_factory=list)
    reviews_screened: int = 0
    modifications_extracted: int = 0

    def summary(self) -> EnhancementSummary:
        total = sum(len(m.changes_made) for m in self.applied)
        types = sorted({m.modification_type for m in self.applied})
        impact = "; ".join(m.summary or m.reasoning for m in self.applied[:3])
        if len(self.applied) > 3:
            impact += f" (and {len(self.applied) - 3} more)"
        if self.applied:
            status, reason = "enhanced", ""
        elif self.modifications_extracted:
            status, reason = "no_tweaks", "modifications were found but none was both applied by its reviewer and generalizable"
        elif self.reviews_screened:
            status, reason = "no_tweaks", "no review describes a modification"
        else:
            status, reason = "no_tweaks", "recipe has no reviews"
        return EnhancementSummary(
            status=status,
            reason=reason,
            total_changes=total,
            change_types=types,
            expected_impact=impact or "No community modifications applied",
            reviews_screened=self.reviews_screened,
            modifications_extracted=self.modifications_extracted,
            modifications_applied=len(self.applied),
            modifications_considered=len(self.considered),
        )


class Composer:
    def __init__(self, modifier: Optional[RecipeModifier] = None):
        self.modifier = modifier or RecipeModifier()

    def compose(self, recipe: Recipe, extractions: List[Tuple[Review, ExtractionResult]]) -> Composition:
        ranked = sorted(extractions, key=lambda pair: rank_key(pair[0]))
        table: Dict[str, List[_Line]] = {
            "ingredients": [_Line(i, t) for i, t in enumerate(recipe.ingredients)],
            "instructions": [_Line(i, t) for i, t in enumerate(recipe.instructions)],
        }
        original = {"ingredients": list(recipe.ingredients), "instructions": list(recipe.instructions)}
        # (target, orig_index) -> the change record that owns the line
        claims: Dict[Tuple[str, int], ChangeRecord] = {}

        comp = Composition(ingredients=[], instructions=[], reviews_screened=len(extractions))
        for review, extraction in ranked:
            comp.modifications_extracted += len(extraction.modifications)
            for mod in extraction.modifications:
                self._place(mod, review, table, original, claims, comp)

        comp.ingredients = [ln.text for ln in table["ingredients"]]
        comp.instructions = [ln.text for ln in table["instructions"]]
        return comp

    # ------------------------------------------------------------------------

    def _place(
        self,
        mod: ModificationObject,
        review: Review,
        table: Dict[str, List[_Line]],
        original: Dict[str, List[str]],
        claims: Dict[Tuple[str, int], ChangeRecord],
        comp: Composition,
    ) -> None:
        source = source_of(review)
        if not mod.should_apply:
            comp.considered.append(ModificationConsidered(
                source_review=source, modification_type=mod.modification_type, summary=mod.summary,
                reasoning=mod.reasoning, reason=mod.exclusion_reason or "excluded",
            ))
            return
        if not mod.edits:
            comp.considered.append(ModificationConsidered(
                source_review=source, modification_type=mod.modification_type, summary=mod.summary,
                reasoning=mod.reasoning, reason="no concrete edit could be derived from the review",
            ))
            return

        # Resolve every edit against the ORIGINAL lines to learn which lines it touches.
        resolved = []
        for edit in mod.edits:
            res = self.modifier.resolve(edit, original[edit.target])
            resolved.append((edit, res))

        # Conflict: a replace/remove on a line another modification already owns.
        for edit, res in resolved:
            if res.status != "ok" or edit.operation == "add_after" or res.index is None:
                continue
            owner = claims.get((edit.target, res.index))
            if owner is not None:
                proposed = self._proposed_text(edit, original[edit.target][res.index])
                same = proposed == owner.to_text
                owner.alternatives.append(Alternative(
                    source_review=source, modification_type=mod.modification_type,
                    summary=mod.summary, proposed_text=proposed,
                ))
                ref = f"{'I' if edit.target == 'ingredients' else 'S'}{res.index}"
                comp.considered.append(ModificationConsidered(
                    source_review=source, modification_type=mod.modification_type, summary=mod.summary,
                    reasoning=mod.reasoning, conflicts_with_line=res.index,
                    reason=(f"same change as a higher-ranked modification on {ref}" if same
                            else f"conflicts with a higher-ranked modification on {ref}; kept as an alternative"),
                ))
                return

        # Apply.
        changes: List[ChangeRecord] = []
        failed: List[str] = []
        for edit, res in resolved:
            if res.status != "ok" or res.index is None:
                failed.append(f"{edit.operation} {edit.line_ref or ''} '{edit.find}': {res.reason}".strip())
                continue
            lines = table[edit.target]
            pos = next((i for i, ln in enumerate(lines) if ln.orig_index == res.index), None)
            if pos is None:
                failed.append(f"{edit.operation} '{edit.find}': line was removed by an earlier edit")
                continue
            kind = "ingredient" if edit.target == "ingredients" else "instruction"
            if edit.operation == "add_after":
                new_lines, result = self.modifier.apply_edit(edit, [ln.text for ln in lines])
                if result.status != "applied":
                    failed.append(f"add_after '{edit.add}': {result.reason}")
                    continue
                lines.insert(pos + 1, _Line(None, edit.add or ""))
                changes.append(ChangeRecord(type=kind, operation="add", line_index=res.index, from_text="", to_text=edit.add or ""))
                continue
            current = lines[pos].text
            span = locate(edit.find, current, self.modifier.fuzzy_threshold)
            if span is None:
                failed.append(f"{edit.operation} '{edit.find}': text no longer present after an earlier edit")
                continue
            new_text, reason = self.modifier.apply_to_line(edit, current, span)
            if reason:
                failed.append(f"{edit.operation} '{edit.find}': {reason}")
                continue
            record = ChangeRecord(
                type=kind, operation="replace" if new_text is not None else "remove",
                line_index=res.index, from_text=current, to_text=new_text or "",
            )
            if new_text is None:
                lines.pop(pos)
            else:
                lines[pos].text = new_text
            changes.append(record)
            claims[(edit.target, res.index)] = record

        if changes:
            comp.applied.append(ModificationApplied(
                source_review=source, modification_type=mod.modification_type, summary=mod.summary,
                reasoning=mod.reasoning, changes_made=changes, edits_failed=failed,
            ))
        else:
            comp.considered.append(ModificationConsidered(
                source_review=source, modification_type=mod.modification_type, summary=mod.summary,
                reasoning=mod.reasoning, reason="no edit could be applied: " + "; ".join(failed),
            ))
            logger.warning(f"modification '{mod.summary}' dropped: {failed}")

    def _proposed_text(self, edit, original_line: str) -> str:
        span = locate(edit.find, original_line, self.modifier.fuzzy_threshold)
        if span is None:
            return edit.replace or ""
        new_text, reason = self.modifier.apply_to_line(edit, original_line, span)
        if reason:
            return edit.replace or ""
        return new_text if new_text is not None else "(remove this line)"
