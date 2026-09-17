"""Shared narration handling for the b-roll and prompter renderers.

The narration lives in video/SCRIPT.md. Scene lengths are derived from it:
a scene lasts as long as its narration takes at PACE words per second, plus
a beat, so the b-roll and the prompter agree by construction.
"""

from __future__ import annotations

import re
from pathlib import Path

PACE = 2.1  # words per second, a comfortable reading speed
MAX_CHUNK_WORDS = 18
MIN_CHUNK_WORDS = 4


def parse_script(path: Path) -> tuple[str, dict[int, str], str]:
    """Return intro text, {scene number: text}, outro text from the blockquotes."""
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    intro, outro, scenes = "", "", {}
    for sec in sections:
        head, _, body = sec.partition("\n")
        quotes = [q[2:].strip() for q in body.splitlines() if q.startswith("> ")]
        narration = re.sub(r"\s*\(\d+ words[^)]*\)\s*$", "", " ".join(quotes)).strip()
        if head.startswith("0:00"):
            intro = narration
        elif head.startswith("Outro"):
            outro = narration
        else:
            m = re.match(r"Scene (\d+)", head)
            if m:
                scenes[int(m.group(1))] = narration
    return intro, scenes, outro


def words(text: str) -> int:
    return len(text.split())


def seconds_for(text: str, pace: float = PACE) -> float:
    return words(text) / pace + 1.0


def chunk(text: str) -> list[str]:
    """Caption-sized pieces: sentences, split at commas only when long, tiny bits merged."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    out: list[str] = []
    for s in sentences:
        if words(s) <= MAX_CHUNK_WORDS:
            out.append(s)
            continue
        cur = ""
        for part in re.split(r"(?<=[,;:])\s+", s):
            if cur and words(cur + " " + part) > MAX_CHUNK_WORDS:
                out.append(cur)
                cur = part
            else:
                cur = (cur + " " + part).strip()
        if cur:
            out.append(cur)
    merged: list[str] = []
    for c in out:
        if merged and (words(c) < MIN_CHUNK_WORDS or words(merged[-1]) < MIN_CHUNK_WORDS) and words(merged[-1] + " " + c) <= MAX_CHUNK_WORDS + 4:
            merged[-1] = merged[-1] + " " + c
        else:
            merged.append(c)
    return merged
