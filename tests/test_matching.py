"""Matcher behaviour beyond the inherited defects. No LLM involved."""

import pytest

from llm_pipeline.models import ModificationEdit
from llm_pipeline.recipe_modifier import RecipeModifier, ingredient_key, locate, normalize


@pytest.fixture
def modifier():
    return RecipeModifier()


def edit(**kw) -> ModificationEdit:
    kw.setdefault("target", "ingredients")
    kw.setdefault("operation", "replace")
    return ModificationEdit(**kw)


# -- normalization ---------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("1/2 tsp salt", "0.5 teaspoon salt"),
    ("1 1/2 cups packed brown sugar", "1.5 cups packed brown sugar"),
    ("2 ⅓ cups mashed bananas", "2.3333332538605 cups mashed bananas"),
    ("1 lb sirloin", "1 pound sirloin"),
    ("350 degrees F", "350 Degrees F"),
    ("confectioners' sugar", "confectioners&#39; sugar"),
])
def test_normalize_equivalences(a, b):
    assert normalize(a) == normalize(b)


# -- locate within a line --------------------------------------------------------

def test_locate_exact_span():
    s = locate("about 10 minutes", "Bake until browned, about 10 minutes.")
    assert (s.start, s.end, s.match) == (20, 36, "exact")


def test_locate_normalized_span_keeps_trailing_punctuation_out():
    line = "Stir in flour, chocolate chips, and walnuts."
    s = locate("Walnuts", line)
    assert line[s.start:s.end] == "walnuts"
    assert s.match == "normalized"


def test_locate_fraction_phrasing_inside_line():
    line = "0.5 teaspoon salt"
    s = locate("1/2 tsp salt", line)
    assert s is not None and line[s.start:s.end] == "0.5 teaspoon salt"


def test_locate_rejects_low_similarity():
    assert locate("1 egg", "2 teaspoons vanilla extract") is None


# -- resolution across lines -----------------------------------------------------

def test_fraction_and_unit_phrasing_resolves_and_replaces(cookies, modifier):
    content, r = modifier.apply_edit(edit(find="1/2 tsp salt", replace="1 1/2 teaspoons salt"), cookies.ingredients)
    assert r.status == "applied" and r.line_index == 7 and r.match == "normalized"
    assert content[7] == "1 1/2 teaspoons salt"


def test_line_ref_disambiguates_a_short_find(cookies, modifier):
    content, r = modifier.apply_edit(edit(line_ref="I2", find="1 cup", replace="1.5 cups"), cookies.ingredients)
    assert r.status == "applied" and r.line_index == 2 and r.match == "line_ref"
    assert content[2] == "1.5 cups packed brown sugar"
    assert content[1] == "1 cup white sugar"


def test_line_ref_with_empty_find_replaces_the_whole_line(cookies, modifier):
    content, r = modifier.apply_edit(edit(line_ref="I10", find="", replace="1 cup chopped pecans"), cookies.ingredients)
    assert r.status == "applied" and content[10] == "1 cup chopped pecans"


def test_wrong_line_ref_falls_back_to_a_unique_exact_match(cookies, modifier):
    content, r = modifier.apply_edit(edit(line_ref="I0", find="3 cups all-purpose flour", replace="2.5 cups all-purpose flour"), cookies.ingredients)
    assert r.status == "applied" and r.line_index == 8


def test_instruction_line_ref_letter_mismatch_is_ignored(cookies, modifier):
    content, r = modifier.apply_edit(
        edit(target="instructions", line_ref="I1", find="350 degrees F", replace="375 degrees F"), cookies.instructions
    )
    assert r.status == "applied" and r.line_index == 1


def test_ambiguous_without_line_ref(cookies, modifier):
    _, r = modifier.apply_edit(edit(find="sugar", replace="sweetener"), cookies.ingredients)
    assert r.status == "ambiguous" and "more than one line" in r.reason


def test_fuzzy_fallback_needs_high_similarity(cookies, modifier):
    _, r = modifier.apply_edit(edit(find="2 cup semisweet chocolate chip", replace="1 cup chips"), cookies.ingredients)
    assert r.status == "applied" and r.line_index == 9 and r.match == "fuzzy"


# -- apply guards ----------------------------------------------------------------

def test_no_change_is_reported_as_failed_not_applied(cookies, modifier):
    _, r = modifier.apply_edit(edit(find="1 cup white sugar", replace="1 cup white sugar"), cookies.ingredients)
    assert r.status == "failed" and "nothing changed" in r.reason


def test_add_that_duplicates_an_existing_ingredient_is_refused(modifier):
    content = ["0.25 cup soy sauce", "1 tablespoon white sugar"]
    new, r = modifier.apply_edit(edit(operation="add_after", line_ref="I0", find="", add="0.5 cup soy sauce"), content)
    assert r.status == "failed" and "duplicates existing line 0" in r.reason
    assert new == content


def test_replacement_that_drops_the_quantity_is_refused(modifier):
    content = ["1.5 teaspoons ground ginger"]
    new, r = modifier.apply_edit(edit(line_ref="I0", find="", replace="Fresh grated ginger"), content)
    assert r.status == "failed" and "drops the quantity" in r.reason


def test_remove_whole_line_and_remove_substring(cookies, modifier):
    content, r = modifier.apply_edit(edit(operation="remove", find="1 cup chopped walnuts"), cookies.ingredients)
    assert r.status == "applied" and len(content) == len(cookies.ingredients) - 1
    content, r = modifier.apply_edit(
        edit(target="instructions", operation="remove", find=", and walnuts"), cookies.instructions
    )
    assert r.status == "applied" and content[4] == "Stir in flour, chocolate chips."


def test_ingredient_key_ignores_quantity_and_unit():
    assert ingredient_key("0.25 cup soy sauce") == ingredient_key("1/2 cup soy sauce")
    assert ingredient_key("1 cup chopped walnuts") != ingredient_key("1 cup chopped pecans")
    assert ingredient_key("1 pinch ground nutmeg") == ingredient_key("0.5 teaspoon ground nutmeg")
