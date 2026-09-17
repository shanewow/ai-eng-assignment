"""LLM Analysis Pipeline - Main Orchestrator

    extract (per review, cached)  ->  compose (per recipe, no model)  ->  emit

A recipe with no applicable modifications still emits a record, with
status "no_tweaks" and a reason. "failed" is reserved for genuine faults
(unreadable file, extraction that never validated).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger

from .composer import Composer
from .enhanced_recipe_generator import EnhancedRecipeGenerator
from .llm_client import DEFAULT_CACHE_DIR, CachedChatClient
from .models import EnhancedRecipe, Recipe, Review
from .prompts import PROMPT_VERSION
from .recipe_modifier import RecipeModifier
from .tweak_extractor import TweakExtractor


def load_recipe_data(file_path: str | Path) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_recipe(data: Dict[str, Any]) -> Recipe:
    return Recipe(
        recipe_id=str(data.get("recipe_id", "unknown")),
        title=data.get("title", "Unknown Recipe"),
        ingredients=data.get("ingredients", []),
        instructions=data.get("instructions", []),
        description=data.get("description"),
        servings=str(data["servings"]) if data.get("servings") is not None else None,
        rating=data.get("rating"),
        prep_time=data.get("preptime"),
        cook_time=data.get("cooktime"),
        total_time=data.get("totaltime"),
        url=data.get("url"),
    )


def parse_reviews(data: Dict[str, Any]) -> List[Review]:
    """Every review with text. Featured status comes from `featured_tweaks`."""
    featured = {t.get("text", "").strip() for t in data.get("featured_tweaks") or []}
    reviews = []
    for index, raw in enumerate(data.get("reviews", [])):
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        reviews.append(Review(
            text=text,
            rating=raw.get("rating"),
            username=raw.get("username"),
            has_modification=bool(raw.get("has_modification", False)),
            is_featured=text in featured or bool(raw.get("is_featured", False)),
            index=index,
        ))
    return reviews


@dataclass
class RecipeResult:
    recipe_file: str
    status: str  # enhanced | no_tweaks | failed
    enhanced: Optional[EnhancedRecipe] = None
    output_path: Optional[str] = None
    error: Optional[str] = None


class LLMAnalysisPipeline:
    """Complete pipeline for analyzing recipes and generating enhanced versions."""

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        output_dir: str | Path = "data/enhanced",
        model: Optional[str] = None,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        use_cache: bool = True,
        pipeline_version: Optional[str] = None,
    ):
        load_dotenv()
        self.output_dir = Path(output_dir)
        self.client = CachedChatClient(cache_dir=cache_dir, api_key=openai_api_key, use_cache=use_cache)
        self.tweak_extractor = TweakExtractor(client=self.client, model=model or os.getenv("OPENAI_MODEL"))
        self.recipe_modifier = RecipeModifier()
        self.composer = Composer(self.recipe_modifier)
        self.enhanced_generator = EnhancedRecipeGenerator(**({"pipeline_version": pipeline_version} if pipeline_version else {}))
        logger.info(f"pipeline model={self.tweak_extractor.model} output_dir={self.output_dir}")

    # -- single recipe -----------------------------------------------------------

    def enhance(self, data: Dict[str, Any]) -> EnhancedRecipe:
        recipe = parse_recipe(data)
        reviews = parse_reviews(data)
        logger.info(f"{recipe.title}: screening {len(reviews)} review(s)")
        extractions = self.tweak_extractor.extract_all(reviews, recipe)
        composition = self.composer.compose(recipe, extractions)
        model = next((e.model for _, e in extractions if e.model), self.tweak_extractor.model)
        return self.enhanced_generator.generate_enhanced_recipe(recipe, composition, model=model, prompt_version=PROMPT_VERSION)

    def process_single_recipe(self, recipe_file: str | Path, save_output: bool = True) -> RecipeResult:
        recipe_file = str(recipe_file)
        try:
            data = load_recipe_data(recipe_file)
            enhanced = self.enhance(data)
        except Exception as exc:
            logger.exception(f"failed: {recipe_file}")
            return RecipeResult(recipe_file=recipe_file, status="failed", error=f"{type(exc).__name__}: {exc}")

        status = enhanced.enhancement_summary.status
        output_path = None
        if save_output:
            recipe = parse_recipe(data)
            output_path = str(self.enhanced_generator.save_enhanced_recipe(
                enhanced, self.output_dir / self.enhanced_generator.output_filename(recipe)
            ))
        s = enhanced.enhancement_summary
        logger.info(
            f"{enhanced.title}: {status} "
            f"({s.modifications_applied} applied, {s.modifications_considered} considered, {s.total_changes} line changes)"
        )
        return RecipeResult(recipe_file=recipe_file, status=status, enhanced=enhanced, output_path=output_path)

    # -- many recipes ------------------------------------------------------------

    def process_recipe_directory(self, data_dir: str | Path = "data") -> List[RecipeResult]:
        files = sorted(Path(data_dir).glob("recipe_*.json"))
        logger.info(f"found {len(files)} recipe file(s) in {data_dir}")
        return [self.process_single_recipe(f) for f in files]

    @staticmethod
    def summarize(results: List[RecipeResult]) -> Dict[str, Any]:
        counts = {k: sum(1 for r in results if r.status == k) for k in ("enhanced", "no_tweaks", "failed")}
        rows = []
        for r in results:
            row: Dict[str, Any] = {"recipe_file": Path(r.recipe_file).name, "status": r.status}
            if r.enhanced:
                s = r.enhanced.enhancement_summary
                row.update(
                    title=r.enhanced.title,
                    reviews_screened=s.reviews_screened,
                    modifications_extracted=s.modifications_extracted,
                    modifications_applied=s.modifications_applied,
                    modifications_considered=s.modifications_considered,
                    line_changes=s.total_changes,
                    change_types=s.change_types,
                    reason=s.reason,
                    output=r.output_path,
                )
            if r.error:
                row["error"] = r.error
            rows.append(row)
        return {"pipeline_summary": {"recipes": len(results), **counts}, "recipes": rows}

    def save_summary_report(self, results: List[RecipeResult], output_path: Optional[str | Path] = None) -> Path:
        path = Path(output_path) if output_path else self.output_dir / "pipeline_summary_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summarize(results), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"saved {path}")
        return path
