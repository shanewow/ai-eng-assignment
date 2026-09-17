"""Step 3: Enhanced Recipe Generation with Attribution

Wraps a Composition into the EnhancedRecipe output shape and writes it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from .composer import Composition
from .models import EnhancedRecipe, Recipe

PIPELINE_VERSION = "2.0.0"


def slugify(title: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:limit].rstrip("-")


class EnhancedRecipeGenerator:
    """Generates enhanced recipes with full citation tracking and attribution."""

    def __init__(self, pipeline_version: str = PIPELINE_VERSION):
        self.pipeline_version = pipeline_version

    def generate_enhanced_recipe(
        self, recipe: Recipe, composition: Composition, model: str = "", prompt_version: str = ""
    ) -> EnhancedRecipe:
        summary = composition.summary()
        suffix = " (Community Enhanced)" if summary.status == "enhanced" else ""
        return EnhancedRecipe(
            recipe_id=f"{recipe.recipe_id}_enhanced",
            original_recipe_id=recipe.recipe_id,
            title=f"{recipe.title}{suffix}",
            ingredients=composition.ingredients,
            instructions=composition.instructions,
            modifications_applied=composition.applied,
            modifications_considered=composition.considered,
            enhancement_summary=summary,
            description=recipe.description,
            servings=recipe.servings,
            prep_time=recipe.prep_time,
            cook_time=recipe.cook_time,
            total_time=recipe.total_time,
            url=recipe.url,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            pipeline_version=self.pipeline_version,
            model=model,
            prompt_version=prompt_version,
        )

    @staticmethod
    def output_filename(recipe: Recipe) -> str:
        return f"enhanced_{recipe.recipe_id}_{slugify(recipe.title)}.json"

    def save_enhanced_recipe(self, enhanced: EnhancedRecipe, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(enhanced.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"saved {path}")
        return path
