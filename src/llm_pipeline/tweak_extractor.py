"""Step 1: Tweak Extraction

Turns one review into zero or more structured modifications. Every review is
screened; the extractor is allowed to return an empty list, so the scraper's
`has_modification` regex is not trusted as a gate (docs/ASSESSMENT.md 5.5).
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

from loguru import logger
from pydantic import ValidationError

from .llm_client import DEFAULT_MODEL, CachedChatClient
from .models import ExtractionResult, ModificationObject, Recipe, Review
from .prompts import EXTRACTION_RESPONSE_FORMAT, PROMPT_VERSION, build_messages, describe, retry_messages


class ExtractionError(RuntimeError):
    """The model never produced a valid extraction for this review."""


class TweakExtractor:
    """Extracts structured modifications from review text using LLM processing."""

    def __init__(
        self,
        client: Optional[CachedChatClient] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_tokens: int = 2000,
        api_key: Optional[str] = None,
    ):
        self.client = client or CachedChatClient(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.reasoning_effort = reasoning_effort or os.getenv("OPENAI_REASONING_EFFORT")
        self.max_tokens = max_tokens
        logger.info(f"TweakExtractor model={self.model} prompt_version={PROMPT_VERSION}")

    def extract(self, review: Review, recipe: Recipe, max_retries: int = 1) -> ExtractionResult:
        messages = build_messages(review, recipe)
        raw = ""
        last_error = ""
        for attempt in range(max_retries + 1):
            result = self.client.complete(
                model=self.model,
                messages=messages,
                response_format=EXTRACTION_RESPONSE_FORMAT,
                temperature=0.0,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )
            raw = result.content
            try:
                data = json.loads(raw)
                extraction = ExtractionResult(modifications=[_clean(m) for m in data.get("modifications", [])])
            except (json.JSONDecodeError, ValidationError, AttributeError, TypeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(f"attempt {attempt + 1}: {last_error}")
                messages = retry_messages(messages, raw, last_error)
                continue
            extraction.raw = raw
            extraction.usage = result.usage
            extraction.cached = result.cached
            extraction.model = result.model
            extraction.prompt_version = PROMPT_VERSION
            logger.info(
                f"{len(extraction.modifications)} modification(s) from review: {describe(review)}"
                + (" [cached]" if result.cached else "")
            )
            return extraction
        raise ExtractionError(last_error)

    def extract_all(self, reviews: List[Review], recipe: Recipe) -> List[Tuple[Review, ExtractionResult]]:
        return [(review, self.extract(review, recipe)) for review in reviews]


_LINE_ID_PREFIX = re.compile(r"^\s*[IS][\d?.]*[a-z]?\s*[:;]\s*")


def _clean(data: dict) -> ModificationObject:
    """Strict schemas require every key. Map empty strings back to None, drop stray
    line ids the model sometimes writes into text fields, and tolerate the two
    text fields being swapped (added text in `replace`, or the reverse)."""
    edits = []
    for e in data.get("edits", []):
        e = dict(e)
        for key in ("find", "replace", "add"):
            if e.get(key):
                e[key] = _LINE_ID_PREFIX.sub("", e[key]).strip()
        replace, add = e.get("replace") or None, e.get("add") or None
        if e.get("operation") == "add_after":
            add = add or replace
            replace = None
            if add and "\n" in add:  # one line per add; the model dumped neighbours
                add = add.split("\n")[0].strip()
        elif e.get("operation") == "replace":
            replace = replace or add
            add = None
        else:
            replace, add = None, None
        e["replace"], e["add"] = replace, add
        edits.append(e)
    data = dict(data)
    data["edits"] = edits
    return ModificationObject(**data)
