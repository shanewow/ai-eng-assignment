"""Ranking, conflict handling and the no-tweaks path. No LLM involved."""

from llm_pipeline.composer import Composer, rank_key
from llm_pipeline.models import ExtractionResult, ModificationEdit, ModificationObject, Review


def review(text, rating=5, featured=False, index=0):
    return Review(text=text, rating=rating, is_featured=featured, index=index)


def mod(summary, *edits, type_="quantity_adjustment", applied=True, generalizable=True):
    return ModificationObject(
        modification_type=type_, summary=summary, reasoning="test",
        was_applied_by_reviewer=applied, is_generalizable=generalizable, edits=list(edits),
    )


def replace(ref, find, new, target="ingredients"):
    return ModificationEdit(target=target, operation="replace", line_ref=ref, find=find, replace=new)


def test_rank_featured_then_rating_then_recency():
    a = review("a", rating=5, featured=False, index=0)
    b = review("b", rating=3, featured=True, index=5)
    c = review("c", rating=5, featured=True, index=2)
    d = review("d", rating=5, featured=True, index=1)
    assert sorted([a, b, c, d], key=rank_key) == [d, c, b, a]


def test_conflict_keeps_winner_and_attaches_loser_as_alternative(cookies):
    winner = review("half the white sugar", rating=5, featured=True, index=1)
    loser = review("more white sugar", rating=5, featured=False, index=0)
    extractions = [
        (loser, ExtractionResult(modifications=[mod("white sugar 1 -> 1.5 cups", replace("I1", "1 cup white sugar", "1.5 cups white sugar"))])),
        (winner, ExtractionResult(modifications=[mod("white sugar 1 -> 1/2 cup", replace("I1", "1 cup white sugar", "0.5 cup white sugar"))])),
    ]
    comp = Composer().compose(cookies, extractions)

    assert comp.ingredients[1] == "0.5 cup white sugar"
    assert len(comp.applied) == 1
    change = comp.applied[0].changes_made[0]
    assert change.line_index == 1 and change.from_text == "1 cup white sugar"
    assert [a.proposed_text for a in change.alternatives] == ["1.5 cups white sugar"]
    assert change.alternatives[0].source_review.text == "more white sugar"
    assert len(comp.considered) == 1
    assert comp.considered[0].conflicts_with_line == 1
    assert "alternative" in comp.considered[0].reason


def test_same_change_from_two_reviews_is_labelled_as_such(cookies):
    r1 = review("2% milk", rating=5, featured=True, index=3)
    r2 = review("2% milk too", rating=4, featured=True, index=4)
    e = lambda: ExtractionResult(modifications=[mod("salt 1 tsp", replace("I7", "0.5 teaspoon salt", "1 teaspoon salt"))])
    comp = Composer().compose(cookies, [(r2, e()), (r1, e())])
    assert comp.applied[0].source_review.text == "2% milk"
    assert comp.considered[0].reason.startswith("same change")


def test_both_flags_required_and_exclusions_are_kept(cookies):
    r = review("next time more salt, and I used margarine because I had it")
    ext = ExtractionResult(modifications=[
        mod("more salt next time", replace("I7", "0.5 teaspoon salt", "1 teaspoon salt"), applied=False),
        mod("margarine for butter", replace("I0", "1 cup butter, softened", "1 cup margarine, softened"), type_="ingredient_substitution", generalizable=False),
    ])
    comp = Composer().compose(cookies, [(r, ext)])
    assert comp.ingredients == cookies.ingredients
    assert comp.applied == []
    reasons = [c.reason for c in comp.considered]
    assert any("not applied by the reviewer" in x for x in reasons)
    assert any("not generalizable" in x for x in reasons)
    assert comp.summary().status == "no_tweaks"


def test_line_indices_survive_inserts_and_removals(cookies):
    r = review("cream of tartar, no water, no nuts", featured=True)
    ext = ExtractionResult(modifications=[
        mod("omit water", ModificationEdit(target="ingredients", operation="remove", line_ref="I6", find="2 teaspoons hot water"), type_="removal"),
        mod("add cream of tartar", ModificationEdit(target="ingredients", operation="add_after", line_ref="I5", find="", add="1 teaspoon cream of tartar"), type_="addition"),
        mod("omit walnuts", ModificationEdit(target="ingredients", operation="remove", line_ref="I10", find="1 cup chopped walnuts"), type_="removal"),
    ])
    comp = Composer().compose(cookies, [(r, ext)])
    assert "2 teaspoons hot water" not in comp.ingredients
    assert "1 cup chopped walnuts" not in comp.ingredients
    assert comp.ingredients[6] == "1 teaspoon cream of tartar"
    assert [c.line_index for m in comp.applied for c in m.changes_made] == [6, 5, 10]


def test_failed_edits_are_reported_not_hidden(cookies):
    r = review("x")
    ext = ExtractionResult(modifications=[
        mod("bogus", replace("I3", "1 egg", "3 eggs")),
        mod("real and bogus", replace("I7", "0.5 teaspoon salt", "1 teaspoon salt"), replace("S9", "no such step", "x", target="instructions")),
    ])
    comp = Composer().compose(cookies, [(r, ext)])
    assert len(comp.applied) == 1 and comp.applied[0].summary == "real and bogus"
    assert len(comp.applied[0].edits_failed) == 1
    assert comp.considered[0].summary == "bogus" and comp.considered[0].reason.startswith("no edit could be applied")


def test_no_reviews_is_no_tweaks_with_reason(cookies):
    comp = Composer().compose(cookies, [])
    s = comp.summary()
    assert s.status == "no_tweaks" and s.reason == "recipe has no reviews"
    assert comp.ingredients == cookies.ingredients
