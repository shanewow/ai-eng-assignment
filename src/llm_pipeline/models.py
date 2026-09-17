"""Pydantic data models for the LLM Analysis Pipeline.

Three layers:

* Extraction (what the model returns): ``ExtractionResult`` holds many
  ``ModificationObject`` per review, each with one category, two apply flags
  and a list of ``ModificationEdit``.
* Application (what the modifier did): ``EditResult`` per edit, with a status
  and a reason. A change record is only ever written for a real change.
* Output (what the product renders): ``EnhancedRecipe`` with applied
  modifications, their line-level changes and alternatives, and the
  modifications that were considered and not applied, each with a reason.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ModificationType = Literal[
    "ingredient_substitution",
    "quantity_adjustment",
    "technique_change",
    "addition",
    "removal",
]


# -- Extraction ----------------------------------------------------------------


class ModificationEdit(BaseModel):
    """Individual atomic edit operation for a recipe modification."""

    target: Literal["ingredients", "instructions"] = Field(
        description="Whether this edit applies to ingredients or instructions"
    )
    operation: Literal["replace", "add_after", "remove"] = Field(
        default="replace",
        description="Type of operation: replace text, add after target, or remove",
    )
    line_ref: str = Field(
        default="",
        description="Reference of the numbered recipe line this edit targets, e.g. 'I3' or 'S6'. For add_after it is the anchor line.",
    )
    find: str = Field(
        default="",
        description="Exact text within the referenced line to replace or remove. Empty means the whole line.",
    )
    replace: Optional[str] = Field(
        default=None, description="Replacement text (required for replace operations)"
    )
    add: Optional[str] = Field(
        default=None, description="Text to add (required for add_after operations)"
    )


class ModificationObject(BaseModel):
    """One discrete modification parsed from a review."""

    modification_type: ModificationType = Field(description="Category of modification")
    summary: str = Field(default="", description="One line, e.g. 'salt 1/2 tsp -> 1 tsp'")
    reasoning: str = Field(description="Why this modification improves the recipe")
    was_applied_by_reviewer: bool = Field(
        default=True,
        description="The reviewer actually made this change, rather than intending or wishing to",
    )
    is_generalizable: bool = Field(
        default=True,
        description="The change would help someone else making this recipe",
    )
    edits: List[ModificationEdit] = Field(description="List of atomic edits to apply")

    @property
    def should_apply(self) -> bool:
        return self.was_applied_by_reviewer and self.is_generalizable

    @property
    def exclusion_reason(self) -> Optional[str]:
        if not self.was_applied_by_reviewer:
            return "not applied by the reviewer (stated intent or suggestion)"
        if not self.is_generalizable:
            return "not generalizable (personal circumstance, accident, or judged worse by the reviewer)"
        return None


class ExtractionResult(BaseModel):
    """Everything the extractor found in one review. May be empty."""

    modifications: List[ModificationObject] = Field(default_factory=list)
    # Run metadata, not part of the model's output schema.
    raw: str = ""
    usage: Dict[str, int] = Field(default_factory=dict)
    cached: bool = False
    model: str = ""
    prompt_version: str = ""


# -- Application ---------------------------------------------------------------


class EditResult(BaseModel):
    """What happened when one edit was applied."""

    edit: ModificationEdit
    status: Literal["applied", "failed", "ambiguous"]
    reason: str = ""
    line_index: Optional[int] = Field(
        default=None, description="Index of the resolved line in the list it was applied to"
    )
    match: Literal["line_ref", "exact", "normalized", "fuzzy", ""] = ""
    score: float = 1.0
    from_text: str = ""
    to_text: str = ""


# -- Output --------------------------------------------------------------------


class SourceReview(BaseModel):
    """Reference to the original review that suggested the modification."""

    text: str = Field(description="Full text of the original review")
    reviewer: Optional[str] = Field(default=None, description="Username of the reviewer")
    rating: Optional[int] = Field(default=None, description="Star rating given by reviewer")
    is_featured: bool = Field(default=False, description="Listed under the recipe's featured tweaks")


class Alternative(BaseModel):
    """A lower-ranked modification that targeted the same line as an applied one."""

    source_review: SourceReview
    modification_type: str
    summary: str
    proposed_text: str = Field(description="What this reviewer would have put on the line")


class ChangeRecord(BaseModel):
    """Record of a specific change made to the recipe."""

    type: Literal["ingredient", "instruction"] = Field(
        description="Type of element that was changed"
    )
    operation: Literal["replace", "add", "remove"] = Field(
        description="Type of operation performed"
    )
    line_index: Optional[int] = Field(
        default=None, description="Index of the line in the ORIGINAL recipe (anchor line for adds)"
    )
    from_text: str = Field(description="Original text before modification")
    to_text: str = Field(description="New text after modification")
    alternatives: List[Alternative] = Field(default_factory=list)


class ModificationApplied(BaseModel):
    """Full record of a modification that was applied to a recipe."""

    source_review: SourceReview
    modification_type: str
    summary: str = ""
    reasoning: str
    changes_made: List[ChangeRecord]
    edits_failed: List[str] = Field(
        default_factory=list, description="Edits of this modification that could not be applied, with reasons"
    )


class ModificationConsidered(BaseModel):
    """A modification that was extracted and deliberately not applied."""

    source_review: SourceReview
    modification_type: str
    summary: str
    reasoning: str = ""
    reason: str = Field(description="Why it was not applied")
    conflicts_with_line: Optional[int] = None


class EnhancementSummary(BaseModel):
    """Summary of all modifications applied to a recipe."""

    status: Literal["enhanced", "no_tweaks"] = "enhanced"
    reason: str = ""
    total_changes: int = Field(description="Total number of changes made")
    change_types: List[str] = Field(description="Types of modifications applied")
    expected_impact: str = Field(description="Expected improvement from these modifications")
    reviews_screened: int = 0
    modifications_extracted: int = 0
    modifications_applied: int = 0
    modifications_considered: int = 0


class EnhancedRecipe(BaseModel):
    """Recipe with community modifications applied and full attribution."""

    recipe_id: str
    original_recipe_id: str
    title: str

    ingredients: List[str]
    instructions: List[str]

    modifications_applied: List[ModificationApplied]
    modifications_considered: List[ModificationConsidered] = Field(default_factory=list)
    enhancement_summary: EnhancementSummary

    description: Optional[str] = None
    servings: Optional[str] = None
    prep_time: Optional[str] = None
    cook_time: Optional[str] = None
    total_time: Optional[str] = None
    url: Optional[str] = None

    created_at: str
    pipeline_version: str = "2.0.0"
    model: str = ""
    prompt_version: str = ""


# -- Input ---------------------------------------------------------------------


class Recipe(BaseModel):
    """Base recipe model for input data."""

    recipe_id: str
    title: str
    ingredients: List[str]
    instructions: List[str]
    description: Optional[str] = None
    servings: Optional[str] = None
    rating: Optional[Dict[str, Any]] = None
    prep_time: Optional[str] = None
    cook_time: Optional[str] = None
    total_time: Optional[str] = None
    url: Optional[str] = None


class Review(BaseModel):
    """Review model for input data."""

    text: str
    rating: Optional[int] = None
    username: Optional[str] = None
    has_modification: bool = False
    is_featured: bool = False
    index: int = Field(default=0, description="Position in the scraped review list; lower is more recent")
