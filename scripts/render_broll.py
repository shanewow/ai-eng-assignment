"""Render the b-roll scenes to MP4 with no screen recorder.

Each scene from scripts/broll.py is captured once (offline, from the cache),
then replayed as a simulated terminal: title card, the command typed out,
the output streamed, then a hold so a voice-over fits. Frames are drawn with
Pillow and piped to ffmpeg.

    uv run python scripts/render_broll.py                 # video/scene-NN.mp4 + video/broll-full.mp4
    uv run python scripts/render_broll.py --scene 8 --hold 40
    uv run python scripts/render_broll.py --fps 30 --size 1920x1080

Edit HOLD_SECONDS to change how long each scene sits on its final frame.
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

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import broll  # noqa: E402

FONT = "/System/Library/Fonts/Menlo.ttc"
HOLD_SECONDS = {1: 30, 2: 12, 3: 8, 4: 36, 5: 14, 6: 43, 7: 16, 8: 22, 9: 20}
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

    def frame(self, cursor: bool = False) -> bytes:
        img = Image.new("RGB", (self.w, self.h), BG)
        draw = ImageDraw.Draw(img)
        visible = self.lines[-self.rows:]
        y = self.pad
        for text, color, bold in visible:
            draw.text((self.pad, y), text, font=self.bold if bold else self.font, fill=color)
            y += self.lh
        if cursor and visible:
            text = visible[-1][0]
            draw.rectangle((self.pad + len(text) * self.cw, y - self.lh + 4, self.pad + (len(text) + 1) * self.cw, y - 2), fill=FG)
        return img.tobytes()


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

    def close(self) -> float:
        self.proc.stdin.close()
        self.proc.wait()
        return self.frames / self.fps


def render_scene(index: int, scene: broll.Scene, output: str, term: Terminal, enc: Encoder) -> None:
    term.clear()
    term.write(scene.title, CYAN, bold=True)
    term.write(scene.caption, DIMC)
    term.write("")
    enc.hold(term.frame(), TITLE_SECONDS)

    command = f"$ {scene.typed()}"
    base = len(term.lines)
    typed = ""
    for ch in command:
        typed += ch
        del term.lines[base:]
        term.write(typed, GREEN)
        enc.hold(term.frame(cursor=True), TYPE_DELAY)
    enc.hold(term.frame(cursor=True), 0.5)

    per_line = 1.0 / STREAM_LINES_PER_SEC
    top_hold = TOP_HOLD_SECONDS.get(index, 0)
    for line in ANSI.sub("", output).rstrip("\n").split("\n"):
        term.write(line, line_color(line))
        if top_hold and len(term.lines) == term.rows:
            enc.hold(term.frame(), top_hold)  # the first screenful, before it scrolls
            top_hold = 0
        enc.hold(term.frame(), per_line)
    enc.hold(term.frame(), HOLD_SECONDS.get(index, 15))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", type=int, action="append")
    parser.add_argument("--hold", type=float, help="override the hold for the chosen scenes")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--font-size", type=int, default=22)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "video")
    args = parser.parse_args(argv)

    w, h = (int(x) for x in args.size.split("x"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="broll-"))
    all_scenes = broll.scenes(str(tmp / "enhanced"), str(tmp / "results"))
    chosen = args.scene or list(range(1, len(all_scenes) + 1))
    if args.hold is not None:
        for i in chosen:
            HOLD_SECONDS[i] = args.hold

    term = Terminal(w, h, args.font_size)
    print(f"terminal {term.cols}x{term.rows} at {w}x{h}, {args.fps} fps")
    manifest = []
    files = []
    for i in chosen:
        scene = all_scenes[i - 1]
        code, output = broll.run_scene(scene, typing=0, pause=0.0, clear=False, quiet=True)
        if code != 0:
            print(f"scene {i} exited {code}; rendering anyway", file=sys.stderr)
        path = args.out_dir / f"scene-{i:02d}.mp4"
        enc = Encoder(path, (w, h), args.fps)
        render_scene(i, scene, output, term, enc)
        seconds = enc.close()
        files.append(path)
        manifest.append({"scene": i, "title": scene.title, "file": path.name, "seconds": round(seconds, 1), "hold": HOLD_SECONDS.get(i, 15)})
        print(f"{path.name}  {seconds:6.1f}s  {scene.title}")

    if len(chosen) == len(all_scenes):
        concat = tmp / "concat.txt"
        concat.write_text("".join(f"file '{p.resolve()}'\n" for p in files))
        full = args.out_dir / "broll-full.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(full)], check=True)
        total = sum(m["seconds"] for m in manifest)
        print(f"{full.name}  {total:6.1f}s  all scenes")
    (args.out_dir / "scenes.json").write_text(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
