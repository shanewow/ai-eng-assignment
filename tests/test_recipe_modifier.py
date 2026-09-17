"""Apply-layer tests. No LLM involved.

Each test is named after the defect it pins down (see docs/ASSESSMENT.md 3.4).
They were written against the inherited RecipeModifier and failed; the same
assertions must hold for the replacement.
"""

from llm_pipeline.models import ModificationEdit, ModificationObject
from llm_pipeline.recipe_modifier import RecipeModifier


def mod(*edits: ModificationEdit) -> ModificationObject:
    return ModificationObject(
        modification_type="quantity_adjustment", reasoning="test", edits=list(edits)
    )


def test_d1_short_find_inside_an_instruction_line_applies(cookies):
    """The starter's own few-shot example 4: change temperature and time."""
    m = mod(
        ModificationEdit(target="instructions", operation="replace",
                         find="350 degrees F", replace="375 degrees F"),
        ModificationEdit(target="instructions", operation="replace",
                         find="about 10 minutes", replace="about 8-9 minutes"),
    )
    modified, changes = RecipeModifier().apply_modification(cookies, m)

    assert "Preheat the oven to 375 degrees F" in modified.instructions[1]
    assert modified.instructions[6].endswith("about 8-9 minutes.")
    assert len(changes) == 2


def test_d2_no_change_record_when_text_did_not_change(cookies):
    """Reviewer writes '1/2 teaspoon'; the recipe says '0.5 teaspoon'."""
    m = mod(
        ModificationEdit(target="ingredients", operation="replace",
                         find="1/2 teaspoon salt", replace="1 teaspoon salt"),
    )
    modified, changes = RecipeModifier().apply_modification(cookies, m)

    for change in changes:
        assert change.from_text != change.to_text, "a change record must describe a real change"
    # Either the edit failed honestly, or normalisation let it through.
    # Both are acceptable; a silent no-op is not.
    if changes:
        assert modified.ingredients[7] == "1 teaspoon salt"
    else:
        assert modified.ingredients == cookies.ingredients


def test_d3_short_find_must_not_resolve_to_a_different_ingredient(cookies):
    """'1 egg' is not '2 eggs'. Rewriting the wrong line corrupts the recipe."""
    m = mod(
        ModificationEdit(target="ingredients", operation="replace",
                         find="1 egg", replace="1 egg plus 1 egg yolk"),
    )
    modified, changes = RecipeModifier().apply_modification(cookies, m)

    assert modified.ingredients[3] == "2 eggs"
    assert changes == []


def test_d3_ambiguous_sugar_line_is_not_guessed(cookies):
    """'1 cup sugar' could be white sugar or brown sugar. Do not pick one."""
    m = mod(
        ModificationEdit(target="ingredients", operation="replace",
                         find="1 cup sugar", replace="0.5 cup sugar"),
    )
    modified, changes = RecipeModifier().apply_modification(cookies, m)

    assert modified.ingredients == cookies.ingredients
    assert changes == []
