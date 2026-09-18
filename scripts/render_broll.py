"""Render the b-roll scenes to MP4 with no screen recorder.

Each scene from scripts/broll.py is captured once (offline, from the cache),
then replayed as a simulated terminal: title card, the command typed out,
the output streamed, then a hold so a voice-over fits. Frames are drawn with
Pillow and piped to ffmpeg.

    uv run python scripts/render_broll.py                 # video/scene-NN.mp4 + video/broll-full.mp4
    uv run python scripts/render_broll.py --scene 8 --hold 40
    uv run python scripts/render_broll.py --fps 30 --size 1920x1080
    uv run python scripts/render_broll.py --captions   # self-contained demo.mp4 with the narration burned in

Scene lengths come from the narration in video/SCRIPT.md: a scene lasts as
long as its words take at narration.PACE words per second. --pace changes
that; --hold forces a fixed end hold for the chosen scenes.
The result is silent; record narration separately and cut to it.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import broll  # noqa: E402
import narration  # noqa: E402

FONT = "/System/Library/Fonts/Menlo.ttc"
MIN_END_HOLD = 3.0                          # never cut a scene off right after its output
HOLD_SECONDS: dict[int, float] = {}         # filled from the narration (words / PACE); --hold overrides
TOP_HOLD_SECONDS = {1: 10, 8: 14, 9: 12}   # pause on the first full screen before scrolling on
TYPE_DELAY = 0.045          # seconds per character
STREAM_LINES_PER_SEC = 30   # how fast output appears
TITLE_SECONDS = 1.8

BG = (24, 26, 30)
FG = (222, 222, 222)
DIMC = (140, 145, 150)
CYAN = (110, 200, 230)
GREEN = (120, 200, 120)
RED = (230, 120, 120)
YELLOW = (230, 200, 110)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CAPTION_BG = (10, 11, 14)
CAPTION_FG = (245, 245, 245)


def wrap_text(draw, text: str, font, max_width: int) -> list[str]:
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


def caption_times(text: str, seconds: float) -> list[tuple[float, float, str]]:
    """(start, end, caption) within a segment, same weighting as the prompter."""
    chunks = narration.chunk(text)
    weights = [len(c.split()) + 1.5 for c in chunks]
    total = sum(weights) or 1
    usable = max(seconds - 1.0, 1.0)
    out, t = [], 0.0
    for c, w in zip(chunks, weights):
        d = usable * w / total
        out.append((t, t + d, c))
        t += d
    return out


def caption_at(captions, t: float) -> Optional[str]:
    for start, end, text in captions:
        if start <= t < end:
            return text
    return captions[-1][2] if captions and t >= captions[-1][1] else None


def line_color(line: str) -> tuple[int, int, int]:
    s = line.lstrip()
    if s.startswith("+ ") or s.startswith("+ after"):
        return GREEN
    if s.startswith("- "):
        return RED
    if s.startswith("alt (") or s.startswith("APPLIED"):
        return CYAN
    if s.startswith("SKIPPED") or s.startswith("!") or "BROKEN" in line:
        return YELLOW
    if s.startswith("ERR") or "Traceback" in line:
        return RED
    return FG


class Terminal:
    def __init__(self, width: int, height: int, font_size: int):
        self.w, self.h = width, height
        self.font = ImageFont.truetype(FONT, font_size)
        self.bold = ImageFont.truetype(FONT, font_size, index=1)
        bbox = self.font.getbbox("M")
        self.cw = bbox[2] - bbox[0]
        self.lh = int(font_size * 1.35)
        self.pad = 48
        self.cols = (self.w - 2 * self.pad) // self.cw
        self.rows = (self.h - 2 * self.pad) // self.lh
        self.lines: list[tuple[str, tuple[int, int, int], bool]] = []
        self.caption_font = ImageFont.truetype(FONT, int(font_size * 1.7), index=1)
        self.caption_lh = int(font_size * 1.7 * 1.3)
        self.caption_rows = 5  # terminal rows given up to the caption band

    def clear(self):
        self.lines = []

    def write(self, text: str, color=FG, bold=False):
        for raw in ANSI.sub("", text).split("\n"):
            raw = raw.expandtabs(4)
            if not raw:
                self.lines.append(("", color, bold))
                continue
            for i in range(0, len(raw), self.cols):
                self.lines.append((raw[i:i + self.cols], color, bold))

    def frame(self, cursor: bool = False, caption: Optional[str] = None) -> bytes:
        img = Image.new("RGB", (self.w, self.h), BG)
        draw = ImageDraw.Draw(img)
        rows = self.rows - (self.caption_rows if caption is not None else 0)
        visible = self.lines[-rows:]
        y = self.pad
        for text, color, bold in visible:
            draw.text((self.pad, y), text, font=self.bold if bold else self.font, fill=color)
            y += self.lh
        if cursor and visible:
            text = visible[-1][0]
            draw.rectangle((self.pad + len(text) * self.cw, y - self.lh + 4, self.pad + (len(text) + 1) * self.cw, y - 2), fill=FG)
        if caption is not None:
            band_h = self.caption_rows * self.lh
            draw.rectangle((0, self.h - band_h, self.w, self.h), fill=CAPTION_BG)
            lines = wrap_text(draw, caption, self.caption_font, self.w - 2 * self.pad)
            cy = self.h - band_h + (band_h - len(lines) * self.caption_lh) / 2
            for line in lines:
                draw.text((self.w / 2, cy), line, font=self.caption_font, fill=CAPTION_FG, anchor="ma")
                cy += self.caption_lh
        return img.tobytes()


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


class Encoder:
    def __init__(self, path: Path, size: tuple[int, int], fps: int):
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE,
        )
        self.fps = fps
        self.frames = 0

    def hold(self, frame: bytes, seconds: float):
        n = max(1, round(seconds * self.fps))
        for _ in range(n):
            self.proc.stdin.write(frame)
        self.frames += n

    def write(self, frame: bytes):
        self.proc.stdin.write(frame)
        self.frames += 1

    def close(self) -> float:
        self.proc.stdin.close()
        self.proc.wait()
        return self.frames / self.fps


def render_scene(index: int, scene: broll.Scene, output: str, term: Terminal, enc: Encoder, target: float, captions=None) -> None:
    started = enc.frames

    def emit(seconds: float, cursor: bool = False) -> None:
        """Write frames for `seconds`, re-drawing whenever the caption changes."""
        if captions is None:
            enc.hold(term.frame(cursor), seconds)
            return
        n = max(1, round(seconds * enc.fps))
        last, frame = object(), b""
        for _ in range(n):
            cap = caption_at(captions, (enc.frames - started) / enc.fps)
            if cap != last:
                frame, last = term.frame(cursor, caption=cap or ""), cap
            enc.write(frame)

    term.clear()
    term.write(scene.title, CYAN, bold=True)
    term.write(scene.caption, DIMC)
    term.write("")
    emit(TITLE_SECONDS)

    command = f"$ {scene.typed()}"
    base = len(term.lines)
    typed = ""
    for ch in command:
        typed += ch
        del term.lines[base:]
        term.write(typed, GREEN)
        emit(TYPE_DELAY, cursor=True)
    emit(0.5, cursor=True)

    per_line = 1.0 / STREAM_LINES_PER_SEC
    top_hold = TOP_HOLD_SECONDS.get(index, 0)
    rows = term.rows - (term.caption_rows if captions is not None else 0)
    for line in ANSI.sub("", output).rstrip("\n").split("\n"):
        term.write(line, line_color(line))
        if top_hold and len(term.lines) == rows:
            emit(top_hold)  # the first screenful, before it scrolls
            top_hold = 0
        emit(per_line)
    elapsed = (enc.frames - started) / enc.fps
    hold = HOLD_SECONDS.get(index)
    if hold is None:
        hold = max(MIN_END_HOLD, target - elapsed)
    emit(hold)


def render_card(enc: Encoder, size: tuple[int, int], title: str, subtitle: str, text: str, seconds: float, font_size: int) -> None:
    """A title card that shows a narration caption by caption. Used for the intro and outro."""
    w, h = size
    big = ImageFont.truetype(FONT, int(font_size * 2.2), index=1)
    body = ImageFont.truetype(FONT, int(font_size * 1.9), index=1)
    small = ImageFont.truetype(FONT, int(font_size * 1.1))
    captions = caption_times(text, seconds)
    n = max(1, round(seconds * enc.fps))
    last, frame = object(), b""
    for i in range(n):
        cap = caption_at(captions, i / enc.fps)
        if cap != last:
            img = Image.new("RGB", (w, h), BG)
            d = ImageDraw.Draw(img)
            d.text((w / 2, h * 0.16), title, font=big, fill=CYAN, anchor="ma")
            d.text((w / 2, h * 0.16 + font_size * 3), subtitle, font=small, fill=DIMC, anchor="ma")
            lines = wrap_text(d, cap or "", body, w - 300)
            lh = int(font_size * 1.9 * 1.35)
            y = h / 2 - lh * len(lines) / 2 + 40
            for line in lines:
                d.text((w / 2, y), line, font=body, fill=FG, anchor="ma")
                y += lh
            frame, last = img.tobytes(), cap
        enc.write(frame)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", type=int, action="append")
    parser.add_argument("--hold", type=float, help="fixed end hold for the chosen scenes instead of deriving it from the narration")
    parser.add_argument("--pace", type=float, default=narration.PACE, help="narration words per second (default %(default)s)")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--font-size", type=int, default=22)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "video")
    parser.add_argument("--captions", action="store_true", help="burn the narration in as captions and add intro/outro cards; writes demo-*.mp4 and demo.mp4")
    args = parser.parse_args(argv)

    w, h = (int(x) for x in args.size.split("x"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="broll-"))
    all_scenes = broll.scenes(str(tmp / "enhanced"), str(tmp / "results"))
    chosen = args.scene or list(range(1, len(all_scenes) + 1))
    if args.hold is not None:
        for i in chosen:
            HOLD_SECONDS[i] = args.hold

    intro_text, scene_text, outro_text = narration.parse_script(ROOT / "video" / "SCRIPT.md")
    prefix = "demo-" if args.captions else ""
    if any(i >= 8 for i in chosen) and 7 not in chosen:
        broll.run_scene(all_scenes[6], typing=0, pause=0.0, clear=False, quiet=True)  # scenes 8 and 9 read scene 7's output
    term = Terminal(w, h, args.font_size)
    print(f"terminal {term.cols}x{term.rows} at {w}x{h}, {args.fps} fps, pace {args.pace} words/s")
    manifest = []
    files = []
    for i in chosen:
        scene = all_scenes[i - 1]
        code, output = broll.run_scene(scene, typing=0, pause=0.0, clear=False, quiet=True)
        if code != 0:
            print(f"scene {i} exited {code}; rendering anyway", file=sys.stderr)
        path = args.out_dir / f"{prefix}scene-{i:02d}.mp4"
        enc = Encoder(path, (w, h), args.fps)
        text = scene_text.get(i, "")
        target = narration.seconds_for(text, args.pace)
        captions = caption_times(text, target) if args.captions else None
        render_scene(i, scene, output, term, enc, target, captions)
        seconds = enc.close()
        files.append(path)
        manifest.append({"scene": i, "title": scene.title, "file": path.name, "seconds": round(seconds, 1), "words": narration.words(text)})
        print(f"{path.name}  {seconds:6.1f}s  ({narration.words(text)} words, {narration.words(text) / seconds:.2f} w/s)  {scene.title}")

    if len(chosen) == len(all_scenes):
        if args.captions:
            cards = []
            for name, title, sub, text in [
                ("intro", "Recipe Enhancement Pipeline", "Casper Studios take-home · Shane Kearney · September 2026", intro_text),
                ("outro", "What I would do next", "docs/ASSESSMENT.md sections 8 and 9", outro_text),
            ]:
                path = args.out_dir / f"{prefix}{name}.mp4"
                enc = Encoder(path, (w, h), args.fps)
                render_card(enc, (w, h), title, sub, text, narration.seconds_for(text, args.pace), args.font_size)
                seconds = enc.close()
                cards.append((name, path, seconds))
                print(f"{path.name}  {seconds:6.1f}s  {title}")
            files = [cards[0][1], *files, cards[1][1]]
        concat = tmp / "concat.txt"
        concat.write_text("".join(f"file '{p.resolve()}'\n" for p in files))
        full = args.out_dir / ("demo.mp4" if args.captions else "broll-full.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(full)], check=True)
        total = duration_of(full)
        print(f"{full.name}  {total:6.1f}s  ({int(total // 60)}:{int(total % 60):02d})")
    if not args.captions:
        (args.out_dir / "scenes.json").write_text(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
