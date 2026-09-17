"""Render a teleprompter video that matches the b-roll timeline.

Reads the narration from video/SCRIPT.md and the scene lengths from
video/scenes.json (written by render_broll.py), then shows each caption at
the moment it should be spoken. Timeline:

    0:00   3 s countdown
    0:03   intro, to camera
    then   scenes 1..9, each exactly as long as its b-roll clip
    then   outro, to camera

So the b-roll starts at 0:03 + intro length; the exact offset is printed and
written to video/timing.txt. Play this on the laptop while recording
yourself, read the captions as they appear, then in the editor line the
recording up with broll-full.mp4 using that offset (or the SRT files).

    uv run python scripts/render_prompter.py
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "video"
FONT = "/System/Library/Fonts/Menlo.ttc"
FPS = 30
W, H = 1920, 1080
COUNTDOWN = 3.0
INTRO_SECONDS = 47.0
OUTRO_SECONDS = 41.0
MAX_CHUNK_WORDS = 14

BG = (18, 20, 24)
FG = (240, 240, 240)
NEXT = (120, 125, 132)
ACCENT = (110, 200, 230)
BAR = (70, 160, 200)


@dataclass
class Segment:
    label: str  # "INTRO — to camera", "SCENE 4 — b-roll", ...
    seconds: float
    text: str


@dataclass
class Caption:
    start: float
    end: float
    text: str
    label: str


def parse_script(path: Path) -> tuple[str, dict[int, str], str]:
    """Return intro text, {scene number: text}, outro text from SCRIPT.md blockquotes."""
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    intro, outro, scenes = "", "", {}
    for sec in sections:
        head, _, body = sec.partition("\n")
        quotes = [q[2:].strip() for q in body.splitlines() if q.startswith("> ")]
        narration = " ".join(quotes)
        narration = re.sub(r"\s*\(\d+ words[^)]*\)\s*$", "", narration).strip()
        if head.startswith("0:00"):
            intro = narration
        elif head.startswith("Outro"):
            outro = narration
        else:
            m = re.match(r"Scene (\d+)", head)
            if m:
                scenes[int(m.group(1))] = narration
    return intro, scenes, outro


def chunk(text: str) -> list[str]:
    """Sentence-sized captions; long sentences split at commas or colons."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    for s in sentences:
        if not s:
            continue
        if len(s.split()) <= MAX_CHUNK_WORDS:
            out.append(s)
            continue
        parts = re.split(r"(?<=[,;:])\s+", s)
        cur = ""
        for p in parts:
            if cur and len((cur + " " + p).split()) > MAX_CHUNK_WORDS:
                out.append(cur)
                cur = p
            else:
                cur = (cur + " " + p).strip()
        if cur:
            out.append(cur)
    return out


def time_segment(seg: Segment, t0: float) -> list[Caption]:
    chunks = chunk(seg.text)
    words = [len(c.split()) for c in chunks]
    total = sum(words) or 1
    usable = max(seg.seconds - 1.0, 1.0)  # leave a beat at the end
    caps, t = [], t0
    for c, w in zip(chunks, words):
        d = usable * w / total
        caps.append(Caption(t, t + d, c, seg.label))
        t += d
    return caps


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(path: Path, caps: list[Caption], offset: float) -> None:
    lines = []
    n = 0
    for c in caps:
        s, e = c.start - offset, c.end - offset
        if e <= 0:
            continue
        n += 1
        lines += [str(n), f"{srt_time(max(s, 0))} --> {srt_time(e)}", c.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    intro, scene_text, outro = parse_script(VIDEO / "SCRIPT.md")
    manifest = json.loads((VIDEO / "scenes.json").read_text())
    segments = [Segment("INTRO — to camera", INTRO_SECONDS, intro)]
    for m in manifest:
        segments.append(Segment(f"SCENE {m['scene']} — b-roll: {m['title'][3:]}", m["seconds"], scene_text[m["scene"]]))
    segments.append(Segment("OUTRO — to camera", OUTRO_SECONDS, outro))

    caps: list[Caption] = []
    boundaries: list[tuple[float, str]] = []
    t = COUNTDOWN
    for seg in segments:
        boundaries.append((t, seg.label))
        caps += time_segment(seg, t)
        t += seg.seconds
    total = t
    broll_start = COUNTDOWN + INTRO_SECONDS
    broll_end = broll_start + sum(m["seconds"] for m in manifest)

    font = ImageFont.truetype(FONT, 44)
    font_bold = ImageFont.truetype(FONT, 44, index=1)
    small = ImageFont.truetype(FONT, 26)
    big = ImageFont.truetype(FONT, 220, index=1)

    out = VIDEO / "prompter.mp4"
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE,
    )
    n_frames = int(total * FPS) + FPS
    for i in range(n_frames):
        now = i / FPS
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        if now < COUNTDOWN:
            n = max(1, math.ceil(COUNTDOWN - now))
            d.text((W / 2, H / 2), str(n), font=big, fill=ACCENT, anchor="mm")
            d.text((W / 2, H / 2 + 200), "recording starts with the intro", font=small, fill=NEXT, anchor="mm")
        else:
            label = next((l for s, l in reversed(boundaries) if now >= s), "")
            cur = next((c for c in caps if c.start <= now < c.end), None)
            idx = caps.index(cur) if cur else None
            nxt = caps[idx + 1] if idx is not None and idx + 1 < len(caps) else None

            d.text((60, 40), label, font=small, fill=ACCENT)
            d.text((W - 60, 40), fmt(now), font=small, fill=NEXT, anchor="ra")
            if broll_start <= now < broll_end:
                d.text((W - 60, 76), f"b-roll {fmt(now - broll_start)}", font=small, fill=NEXT, anchor="ra")
            if cur:
                lines = wrap(d, cur.text, font_bold, W - 240)
                y = H / 2 - 30 * len(lines) - 40
                for line in lines:
                    d.text((W / 2, y), line, font=font_bold, fill=FG, anchor="ma")
                    y += 60
                # progress of the current caption
                frac = (now - cur.start) / max(cur.end - cur.start, 0.01)
                d.rectangle((120, H - 140, 120 + (W - 240) * frac, H - 132), fill=BAR)
                d.rectangle((120, H - 140, W - 120, H - 132), outline=NEXT)
            if nxt:
                lines = wrap(d, nxt.text, font, W - 400)
                y = H / 2 + 120
                for line in lines[:3]:
                    d.text((W / 2, y), line, font=font, fill=NEXT, anchor="ma")
                    y += 56
            # segment boundary flash
            for s, l in boundaries:
                if 0 <= now - s < 1.0:
                    d.text((W / 2, H - 80), f"▶ {l}", font=small, fill=ACCENT, anchor="mm")
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()

    write_srt(VIDEO / "narration-prompter.srt", caps, 0.0)
    write_srt(VIDEO / "narration-broll.srt", [c for c in caps if broll_start <= c.start < broll_end], broll_start)
    timing = [
        f"prompter.mp4 length      {fmt(total)}",
        f"countdown                0:00 - {fmt(COUNTDOWN)}",
        f"intro (to camera)        {fmt(COUNTDOWN)} - {fmt(broll_start)}",
        f"b-roll starts at         {fmt(broll_start)}  <- line broll-full.mp4 up here",
        f"b-roll ends at           {fmt(broll_end)}",
        f"outro (to camera)        {fmt(broll_end)} - {fmt(total)}",
        "",
        "scene starts in prompter time / in b-roll time:",
    ]
    for s, l in boundaries:
        if "SCENE" in l:
            timing.append(f"  {fmt(s)} / {fmt(s - broll_start)}   {l}")
    timing += ["", "narration-prompter.srt: captions on the prompter timeline", "narration-broll.srt: the same captions on the broll-full.mp4 timeline"]
    (VIDEO / "timing.txt").write_text("\n".join(timing) + "\n")
    print("\n".join(timing))
    print(f"\nwrote {out.name}, {len(caps)} captions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
