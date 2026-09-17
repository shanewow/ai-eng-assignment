import json
from pathlib import Path

import pytest
from loguru import logger

from llm_pipeline.models import Recipe

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

logger.remove()  # keep test output to the assertions


def load_recipe(name: str) -> Recipe:
    data = json.loads((DATA / name).read_text(encoding="utf-8"))
    return Recipe(
        recipe_id=data["recipe_id"],
        title=data["title"],
        ingredients=data["ingredients"],
        instructions=data["instructions"],
    )


@pytest.fixture
def cookies() -> Recipe:
    return load_recipe("recipe_10813_best-chocolate-chip-cookies.json")
