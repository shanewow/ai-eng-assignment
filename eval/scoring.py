"""Scoring for the modification eval.

The unit of comparison is an edit outcome: what the system tried to do to
which recipe line, and what actually happened. Adapters (see adapters.py)
produce these for the inherited pipeline and for the rewritten one, so the
same scorer measures both.

Metrics (all rates are 0..1):

  mod_recall           expected must-apply modifications that at least one
                       intended edit reached (extraction quality)
  mod_delivered        expected must-apply modifications that an APPLIED edit
                       reached, i.e. the change is in the output (end to end)
  edit_precision       intended edits that correspond to some acceptable
                       modification (must-apply or optional)
  false_apply_rate     expected must-NOT-apply modifications (future intent,
                       personal circumstance, judged bad) that an intended
                       edit reached anyway
  excluded_rate        must-not-apply modifications the extractor found and
                       deliberately did not apply (the "considered, not
                       applied" panel); only meaningful for the new pipeline
  edit_apply_rate      intended edits whose text actually changed
  noop_rate            change records whose before and after text are equal
  wrong_line_rate      applied replace/remove edits on a line no labelled
                       modification mentions
  contradictory_add_rate  applied adds that duplicate an existing ingredient
                       (a second soy sauce line), i.e. an insert where a
                       replace was meant
  quantity_dropped_rate   applied ingredient replaces that removed the amount
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class EditOutcome:
    target: str  # ingredients | instructions
    operation: str  # replace | add_after | remove
    find: str
    new_text: str  # replacement or added text; "" for remove
    line_ref: Optional[str]  # resolved line, e.g. "I7"; None if unresolved
    status: str  # applied | noop | failed | ambiguous | excluded
    intended: bool  # the system meant to apply this edit
    mod_index: int = 0
    from_text: str = ""
    to_text: str = ""
    reason: str = ""


@dataclass
class CaseRun:
    case_id: str
    outcomes: list[EditOutcome] = field(default_factory=list)
    mods_returned: int = 0
    error: Optional[str] = None
    usage: dict = field(default_factory=dict)
    cached: bool = False
    raw: Optional[str] = None


# -- helpers -------------------------------------------------------------------

_QTY_PREFIX = re.compile(
    r"^\s*(?:[\d\s./⅓⅔¼½¾⅛-]+)?\s*(?:cups?|cup|tablespoons?|tbsp|teaspoons?|tsp|pounds?|lbs?|lb|ounces?|oz|pinch|large|small|medium|cloves?)?\s*",
    re.IGNORECASE,
)


def ingredient_key(text: str) -> str:
    """Strip quantity and unit so '0.5 cup soy sauce' and '0.25 cup soy sauce' compare equal."""
    stripped = _QTY_PREFIX.sub("", text.strip().lower(), count=1)
    return re.sub(r"[^a-z]+", " ", stripped).strip()


def _hit(edit: EditOutcome, expected: dict) -> bool:
    text = edit.new_text.lower()
    if edit.operation in ("replace", "remove") and edit.line_ref in expected.get("lines", []):
        return True
    return any(k.lower() in text for k in expected.get("added_contains", []))


def _must_apply(e: dict) -> bool:
    return e["applied_by_reviewer"] and e["generalizable"] and not e.get("optional")


def _must_not_apply(e: dict) -> bool:
    return (not e["applied_by_reviewer"] or not e["generalizable"]) and not e.get("optional")


# -- per-case ------------------------------------------------------------------


@dataclass
class CaseScore:
    case_id: str
    error: bool = False
    expected_must_apply: int = 0
    found_must_apply: int = 0
    delivered_must_apply: int = 0
    expected_must_not: int = 0
    false_applied: int = 0
    excluded_correctly: int = 0
    intended_edits: int = 0
    precise_edits: int = 0
    applied_edits: int = 0
    records: int = 0
    noop_records: int = 0
    applied_line_edits: int = 0
    wrong_line_edits: int = 0
    applied_adds: int = 0
    contradictory_adds: int = 0
    applied_ingredient_replaces: int = 0
    quantity_dropped: int = 0
    mods_returned: int = 0


def score_case(case: dict, run: CaseRun, ingredients: list[str]) -> CaseScore:
    s = CaseScore(case_id=case["id"], error=run.error is not None, mods_returned=run.mods_returned)
    expected = case["expected"]
    intended = [o for o in run.outcomes if o.intended]
    excluded = [o for o in run.outcomes if not o.intended]

    for e in expected:
        if _must_apply(e):
            s.expected_must_apply += 1
            if any(_hit(o, e) for o in intended):
                s.found_must_apply += 1
            if any(_hit(o, e) for o in intended if o.status == "applied"):
                s.delivered_must_apply += 1
        elif _must_not_apply(e):
            s.expected_must_not += 1
            if any(_hit(o, e) for o in intended):
                s.false_applied += 1
            elif any(_hit(o, e) for o in excluded):
                s.excluded_correctly += 1

    acceptable = [e for e in expected if e["applied_by_reviewer"] and e["generalizable"]]
    all_lines = {ln for e in expected for ln in e.get("lines", []) + e.get("steps", [])}
    ingredient_keys = {ingredient_key(x) for x in ingredients}

    for o in intended:
        s.intended_edits += 1
        if any(_hit(o, e) for e in acceptable):
            s.precise_edits += 1
        if o.status in ("applied", "noop"):
            s.records += 1
        if o.status == "noop":
            s.noop_records += 1
        if o.status != "applied":
            continue
        s.applied_edits += 1
        if o.operation in ("replace", "remove"):
            s.applied_line_edits += 1
            if o.line_ref not in all_lines:
                s.wrong_line_edits += 1
        if o.operation == "add_after" and o.target == "ingredients":
            s.applied_adds += 1
            if ingredient_key(o.new_text) in ingredient_keys:
                s.contradictory_adds += 1
        if o.operation == "replace" and o.target == "ingredients":
            s.applied_ingredient_replaces += 1
            if re.match(r"^\s*[\d⅓⅔¼½¾⅛]", o.from_text) and not re.search(r"[\d⅓⅔¼½¾⅛]", o.to_text):
                s.quantity_dropped += 1
    return s


# -- aggregate -----------------------------------------------------------------


def _rate(num: int, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 3)


def aggregate(scores: list[CaseScore], runs: list[CaseRun]) -> dict:
    t = {k: sum(getattr(s, k) for s in scores) for k in CaseScore.__dataclass_fields__ if k not in ("case_id", "error")}
    usage_in = sum(r.usage.get("prompt_tokens", 0) for r in runs)
    usage_out = sum(r.usage.get("completion_tokens", 0) for r in runs)
    return {
        "cases": len(scores),
        "llm_errors": sum(1 for s in scores if s.error),
        "mods_returned": t["mods_returned"],
        "mod_recall": _rate(t["found_must_apply"], t["expected_must_apply"]),
        "mod_delivered": _rate(t["delivered_must_apply"], t["expected_must_apply"]),
        "edit_precision": _rate(t["precise_edits"], t["intended_edits"]),
        "false_apply_rate": _rate(t["false_applied"], t["expected_must_not"]),
        "excluded_rate": _rate(t["excluded_correctly"], t["expected_must_not"]),
        "edit_apply_rate": _rate(t["applied_edits"], t["intended_edits"]),
        "noop_rate": _rate(t["noop_records"], t["records"]),
        "wrong_line_rate": _rate(t["wrong_line_edits"], t["applied_line_edits"]),
        "contradictory_add_rate": _rate(t["contradictory_adds"], t["applied_adds"]),
        "quantity_dropped_rate": _rate(t["quantity_dropped"], t["applied_ingredient_replaces"]),
        "counts": t,
        "tokens": {"prompt": usage_in, "completion": usage_out, "cached_responses": sum(1 for r in runs if r.cached)},
    }


METRIC_ROWS = [
    ("mod_recall", "Modification recall (extracted)", "higher"),
    ("mod_delivered", "Modification delivered (in output)", "higher"),
    ("edit_precision", "Edit precision", "higher"),
    ("false_apply_rate", "False-apply rate (intent / circumstance)", "lower"),
    ("excluded_rate", "Found and correctly excluded", "higher"),
    ("edit_apply_rate", "Edit apply rate", "higher"),
    ("noop_rate", "Silent no-op rate", "lower"),
    ("wrong_line_rate", "Wrong-line rate", "lower"),
    ("contradictory_add_rate", "Contradictory-add rate", "lower"),
    ("quantity_dropped_rate", "Quantity-dropped rate", "lower"),
]


def fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def render_table(columns: list[tuple[str, dict]]) -> str:
    """Markdown table: one column per (label, aggregate)."""
    head = "| Metric | " + " | ".join(label for label, _ in columns) + " |"
    sep = "|---|" + "|".join("---:" for _ in columns) + "|"
    rows = [head, sep]
    for key, label, _ in METRIC_ROWS:
        rows.append(f"| {label} | " + " | ".join(fmt(agg[key]) for _, agg in columns) + " |")
    rows.append("| Cases with an LLM error | " + " | ".join(str(agg["llm_errors"]) for _, agg in columns) + " |")
    rows.append("| Modifications returned | " + " | ".join(str(agg["mods_returned"]) for _, agg in columns) + " |")
    return "\n".join(rows)


def outcome_dict(o: EditOutcome) -> dict:
    return asdict(o)
