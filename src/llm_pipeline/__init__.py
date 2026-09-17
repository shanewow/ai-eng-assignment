"""
LLM Analysis Pipeline for Recipe Enhancement

1. Tweak extraction: every review -> zero or more structured modifications (LLM, cached)
2. Composition: rank, resolve conflicts, apply edits (no LLM)
3. Enhanced recipe generation: output with attribution, alternatives and exclusions
"""

from .composer import Composer
from .enhanced_recipe_generator import EnhancedRecipeGenerator
from .models import (
    EnhancedRecipe,
    EnhancementSummary,
    ExtractionResult,
    ModificationApplied,
    ModificationEdit,
    ModificationObject,
)
from .pipeline import LLMAnalysisPipeline
from .recipe_modifier import RecipeModifier
from .tweak_extractor import TweakExtractor

__all__ = [
    "Composer",
    "EnhancedRecipe",
    "EnhancedRecipeGenerator",
    "EnhancementSummary",
    "ExtractionResult",
    "LLMAnalysisPipeline",
    "ModificationApplied",
    "ModificationEdit",
    "ModificationObject",
    "RecipeModifier",
    "TweakExtractor",
]
