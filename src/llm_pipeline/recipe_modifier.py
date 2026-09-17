"""Step 2: Recipe Modification

Applies structured edits to recipe lines and reports, per edit, exactly what
happened. Matching strategy, in order:

1. The edit's ``line_ref`` (``I3``, ``S6``) if it names a line that contains
   the ``find`` text.
2. Exact substring across all lines.
3. Normalized substring: case, HTML entities, unicode and ASCII fractions,
   mixed numbers, unit synonyms (tsp/teaspoon, lb/pound), plural units.
4. Fuzzy, only as a fallback: the best token window of each line is compared
   with the find text; the winner must score at least ``fuzzy_threshold`` and
   beat the runner-up line by at least ``ambiguity_margin``.

A match that would hit more than one line is reported as ``ambiguous`` and
nothing is changed. A replace whose result equals the original is reported as
``failed``. A change record is never written for a change that did not happen.
"""

from __future__ import annotations

import copy
import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from loguru import logger

from .models import ChangeRecord, EditResult, ModificationEdit, ModificationObject, Recipe

# -- normalization -------------------------------------------------------------

UNICODE_FRACTIONS = {
    "¼": "1/4", "½": "1/2", "¾": "3/4", "⅓": "1/3", "⅔": "2/3",
    "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}
UNIT_SYNONYMS = {
    "tsp": "teaspoon", "tsps": "teaspoon", "teaspoons": "teaspoon", "t": "teaspoon",
    "tbsp": "tablespoon", "tbsps": "tablespoon", "tablespoons": "tablespoon", "tbs": "tablespoon",
    "cups": "cup", "c": "cup",
    "lb": "pound", "lbs": "pound", "pounds": "pound",
    "oz": "ounce", "ounces": "ounce",
    "g": "gram", "grams": "gram", "kg": "kilogram",
    "ml": "milliliter", "l": "liter",
    "degrees": "degree", "°": "degree",
    "mins": "minute", "min": "minute", "minutes": "minute",
    "hrs": "hour", "hr": "hour", "hours": "hour",
    "pkg": "package", "packages": "package",
}
_MIXED = re.compile(r"(?<!\S)(\d+)\s+(\d+)/(\d+)(?!\S)")
_FRAC = re.compile(r"(?<![\d.])(\d+)/(\d+)(?![\d.])")
_DEC = re.compile(r"\d+\.\d+")
_TOKEN = re.compile(r"\S+")
_PUNCT_EDGE = re.compile(r"^[^\w]+|[^\w]+$")


def _num(value: float) -> str:
    return f"{round(value, 3):g}"


def normalize(text: str) -> str:
    """Canonical form used for comparison. Not for display."""
    s = html.unescape(text).lower()
    for ch, frac in UNICODE_FRACTIONS.items():
        s = s.replace(ch, f" {frac}").replace(f"  {frac}", f" {frac}")
    s = s.replace("°", " degree ")
    s = _MIXED.sub(lambda m: _num(int(m[1]) + int(m[2]) / int(m[3])), s)
    s = _FRAC.sub(lambda m: _num(int(m[1]) / int(m[2])) if int(m[2]) else m[0], s)
    s = _DEC.sub(lambda m: _num(float(m[0])), s)
    tokens = []
    for tok in _TOKEN.findall(s):
        core = _PUNCT_EDGE.sub("", tok)
        if not core:
            continue
        tokens.append(UNIT_SYNONYMS.get(core, core))
    return " ".join(tokens)


@dataclass
class _Tok:
    norm: str
    start: int
    end: int


def _tokens_with_spans(line: str) -> List[_Tok]:
    """Normalized tokens of a line, each with the char span of its core text."""
    unescaped = html.unescape(line)
    if unescaped != line:  # spans would not line up; fall back to the raw text
        unescaped = line
    raw = [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(unescaped)]
    toks: List[_Tok] = []
    i = 0
    while i < len(raw):
        text, start, end = raw[i]
        lead = len(text) - len(text.lstrip("([\"'"))
        trail = len(text) - len(text.rstrip(")]\"'.,;:!?"))
        core = text[lead: len(text) - trail] if trail else text[lead:]
        cs, ce = start + lead, end - trail
        # mixed number: "1 1/2" -> one token
        if core.isdigit() and i + 1 < len(raw) and re.fullmatch(r"\d+/\d+[.,;:!?)]*", raw[i + 1][0]):
            nxt = raw[i + 1]
            core_next = _PUNCT_EDGE.sub("", nxt[0])
            n, d = core_next.split("/")
            toks.append(_Tok(_num(int(core) + int(n) / int(d)), cs, nxt[1] + len(core_next)))
            i += 2
            continue
        norm = normalize(core)
        if norm:
            toks.append(_Tok(norm, cs, ce))
        i += 1
    return toks


# -- locating text within a line -----------------------------------------------


@dataclass
class Span:
    start: int
    end: int
    match: str  # exact | normalized | fuzzy
    score: float = 1.0


def locate(find: str, line: str, fuzzy_threshold: float = 0.85) -> Optional[Span]:
    """Find `find` inside `line`, returning the char span to replace."""
    if not find:
        return Span(0, len(line), "exact")
    idx = line.find(find)
    if idx >= 0:
        return Span(idx, idx + len(find), "exact")
    idx = line.lower().find(find.lower())
    if idx >= 0:
        return Span(idx, idx + len(find), "normalized")

    nfind = normalize(find)
    if not nfind:
        return None
    ftoks = nfind.split()
    toks = _tokens_with_spans(line)
    if not toks:
        return None
    if " ".join(t.norm for t in toks) == nfind:
        return Span(0, len(line), "normalized")

    n = len(ftoks)
    for i in range(0, len(toks) - n + 1):
        if [t.norm for t in toks[i:i + n]] == ftoks:
            return Span(toks[i].start, toks[i + n - 1].end, "normalized")

    best: Optional[Span] = None
    for width in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(0, len(toks) - width) + 1):
            window = toks[i:i + width]
            if not window:
                continue
            score = SequenceMatcher(None, nfind, " ".join(t.norm for t in window)).ratio()
            if score >= fuzzy_threshold and (best is None or score > best.score):
                best = Span(window[0].start, window[-1].end, "fuzzy", score)
    return best


# -- resolution across lines ---------------------------------------------------


@dataclass
class Resolution:
    status: str  # ok | failed | ambiguous
    index: Optional[int] = None
    span: Optional[Span] = None
    match: str = ""
    score: float = 0.0
    reason: str = ""


def parse_line_ref(ref: str, target: str) -> Optional[int]:
    m = re.fullmatch(r"\s*([IS])?\s*(\d+)\s*", ref or "", re.IGNORECASE)
    if not m:
        return None
    letter = (m.group(1) or "").upper()
    expected = "I" if target == "ingredients" else "S"
    if letter and letter != expected:
        return None
    return int(m.group(2))


class RecipeModifier:
    """Applies structured modifications to recipes and reports per-edit outcomes."""

    def __init__(self, fuzzy_threshold: float = 0.85, ambiguity_margin: float = 0.05):
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_margin = ambiguity_margin

    # -- resolve ---------------------------------------------------------------

    def resolve(self, edit: ModificationEdit, content: List[str]) -> Resolution:
        """Decide which line an edit targets, without changing anything."""
        if not content:
            return Resolution("failed", reason="no lines to search")

        ref = parse_line_ref(edit.line_ref, edit.target)
        if ref is not None and 0 <= ref < len(content):
            span = locate(edit.find, content[ref], self.fuzzy_threshold)
            if span is not None and (span.match != "fuzzy" or not edit.find):
                return Resolution("ok", ref, span, "line_ref", span.score)
        elif ref is not None:
            logger.debug(f"line_ref {edit.line_ref!r} out of range for {edit.target}")

        if not edit.find:
            if edit.operation == "add_after" and edit.target == "ingredients":
                last = len(content) - 1  # no anchor given: a new ingredient goes at the end
                return Resolution("ok", last, Span(0, len(content[last]), "exact"), "line_ref", 1.0)
            return Resolution("failed", reason="no find text and no valid line reference")

        candidates: List[Tuple[int, Span]] = []
        for i, line in enumerate(content):
            span = locate(edit.find, line, self.fuzzy_threshold)
            if span is not None:
                candidates.append((i, span))
        if not candidates:
            return Resolution("failed", reason=f"'{edit.find}' not found in {edit.target}")

        rank = {"exact": 3, "normalized": 2, "fuzzy": 1}
        candidates.sort(key=lambda c: (rank[c[1].match], c[1].score), reverse=True)
        top_i, top = candidates[0]
        if len(candidates) > 1:
            second_i, second = candidates[1]
            same_tier = rank[second.match] == rank[top.match]
            if same_tier and (top.match != "fuzzy" or top.score - second.score < self.ambiguity_margin):
                return Resolution(
                    "ambiguous",
                    reason=f"'{edit.find}' matches more than one line ({top_i} and {second_i}); no line_ref to disambiguate",
                )
        return Resolution("ok", top_i, top, top.match, top.score)

    # -- apply -----------------------------------------------------------------

    @staticmethod
    def find_duplicate(text: str, content: List[str], target: str = "ingredients") -> Optional[int]:
        """Index of an existing line that the added text would duplicate, else None."""
        if target != "ingredients":
            return content.index(text) if text in content else None
        key = ingredient_key(text)
        if not key:
            return None
        for i, line in enumerate(content):
            if ingredient_key(line) == key:
                return i
        return None

    def apply_to_line(self, edit: ModificationEdit, line: str, span: Span) -> Tuple[Optional[str], str]:
        """Return (new_line or None to delete, failure reason or '')."""
        if edit.operation == "replace":
            if edit.replace is None:
                return None, "replace operation has no replacement text"
            new = line[: span.start] + edit.replace + line[span.end:]
            if new == line:
                return None, "replacement text equals the original; nothing changed"
            if (
                edit.target == "ingredients"
                and span.start == 0 and span.end == len(line)
                and re.match(r"^\s*[\d¼½¾⅓⅔⅛]", line)
                and not re.search(r"[\d¼½¾⅓⅔⅛]", edit.replace)
                and "to taste" not in edit.replace.lower()
            ):
                return None, "replacement drops the quantity from an ingredient line"
            return new, ""
        if edit.operation == "remove":
            if span.start == 0 and span.end == len(line):
                return None, ""
            new = (line[: span.start] + line[span.end:])
            new = re.sub(r"\s{2,}", " ", new)
            new = re.sub(r"\s+([,.;:])", r"\1", new)
            new = re.sub(r",\s*([.;:])", r"\1", new).strip()
            if new == line:
                return None, "nothing to remove"
            return new, ""
        return None, f"unsupported operation {edit.operation}"

    def apply_edit(self, edit: ModificationEdit, content: List[str]) -> Tuple[List[str], EditResult]:
        """Apply a single edit to a list of lines. Never mutates the input."""
        working = list(content)
        res = self.resolve(edit, working)
        if res.status != "ok":
            return working, EditResult(edit=edit, status=res.status, reason=res.reason)  # type: ignore[arg-type]

        index, span = res.index, res.span
        assert index is not None and span is not None
        kind = "ingredient" if edit.target == "ingredients" else "instruction"

        if edit.operation == "add_after":
            if not edit.add or not edit.add.strip():
                return working, EditResult(edit=edit, status="failed", reason="add_after has no text to add", line_index=index)
            dup = self.find_duplicate(edit.add, working, edit.target)
            if dup is not None:
                return working, EditResult(
                    edit=edit, status="failed", line_index=index,
                    reason=f"added {kind} duplicates existing line {dup} ('{working[dup]}'); use replace instead",
                )
            working.insert(index + 1, edit.add)
            return working, EditResult(
                edit=edit, status="applied", line_index=index, match=res.match, score=res.score,
                from_text="", to_text=edit.add,
            )

        original = working[index]
        new_line, reason = self.apply_to_line(edit, original, span)
        if reason:
            return working, EditResult(edit=edit, status="failed", reason=reason, line_index=index, match=res.match, score=res.score)
        if new_line is None:
            working.pop(index)
            return working, EditResult(edit=edit, status="applied", line_index=index, match=res.match, score=res.score, from_text=original, to_text="")
        working[index] = new_line
        return working, EditResult(edit=edit, status="applied", line_index=index, match=res.match, score=res.score, from_text=original, to_text=new_line)

    def apply_modification_detailed(
        self, recipe: Recipe, modification: ModificationObject
    ) -> Tuple[Recipe, List[EditResult]]:
        """Apply every edit of one modification sequentially; report each."""
        modified = recipe.model_copy(deep=True)
        results: List[EditResult] = []
        for edit in modification.edits:
            content = modified.ingredients if edit.target == "ingredients" else modified.instructions
            new_content, result = self.apply_edit(edit, content)
            if edit.target == "ingredients":
                modified.ingredients = new_content
            else:
                modified.instructions = new_content
            results.append(result)
            if result.status != "applied":
                logger.debug(f"edit {result.status}: {result.reason}")
        return modified, results

    def apply_modification(
        self, recipe: Recipe, modification: ModificationObject
    ) -> Tuple[Recipe, List[ChangeRecord]]:
        """Compatibility entry point: change records for edits that really applied."""
        modified, results = self.apply_modification_detailed(recipe, modification)
        return modified, [to_change_record(r) for r in results if r.status == "applied"]


def to_change_record(result: EditResult) -> ChangeRecord:
    op = {"replace": "replace", "add_after": "add", "remove": "remove"}[result.edit.operation]
    return ChangeRecord(
        type="ingredient" if result.edit.target == "ingredients" else "instruction",
        operation=op,  # type: ignore[arg-type]
        line_index=result.line_index,
        from_text=result.from_text,
        to_text=result.to_text,
    )


_QTY_PREFIX = re.compile(
    r"^\s*(?:[\d\s./¼½¾⅓⅔⅛-]+)?\s*(?:cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|lbs?|lb|ounces?|oz|pinch|dash|large|small|medium|cloves?)?\s*(?:of\s+)?",
    re.IGNORECASE,
)


def ingredient_key(text: str) -> str:
    """'0.5 cup soy sauce' and '1/4 cup soy sauce' share a key; used to catch duplicate inserts."""
    stripped = _QTY_PREFIX.sub("", html.unescape(text).strip().lower(), count=1)
    stripped = re.sub(r"\(.*?\)", " ", stripped)
    stripped = stripped.split(",")[0]
    return re.sub(r"[^a-z]+", " ", stripped).strip()


__all__ = ["RecipeModifier", "locate", "normalize", "ingredient_key", "to_change_record", "Resolution", "Span"]
