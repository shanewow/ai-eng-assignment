"""Frozen copy of the inherited pipeline (commit f0c2688).

Used by the eval harness (`--legacy`), by scripts/repro_inherited_defects.py and
by scripts/run_legacy_baseline.py so the "before" numbers and outputs can be
regenerated after the real package was rewritten. Nothing here is edited.
"""

from .models import ModificationEdit, ModificationObject, Recipe, Review
from .pipeline import LLMAnalysisPipeline
from .prompts import FEW_SHOT_EXAMPLES, build_simple_prompt
from .recipe_modifier import RecipeModifier
from .tweak_extractor import TweakExtractor

__all__ = [
    "FEW_SHOT_EXAMPLES",
    "LLMAnalysisPipeline",
    "ModificationEdit",
    "ModificationObject",
    "Recipe",
    "RecipeModifier",
    "Review",
    "TweakExtractor",
    "build_simple_prompt",
]
