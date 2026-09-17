"""Combine the camera recording with the b-roll into the final video.

    uv run python scripts/assemble_video.py video/camera.mov
    uv run python scripts/assemble_video.py video/camera.mov --broll-at 38
    uv run python scripts/assemble_video.py video/camera.mov --pip none

Layout: you full-frame for the intro and outro; the b-roll full-frame with
you picture-in-picture (bottom right) while it plays. Audio is yours
throughout. Output is video/kearney-recipe-pipeline.mp4 at 1080p.

Sync: by default the script finds the first moment you speak (ffmpeg
silencedetect) and starts the b-roll 47 s later, which is the intro length
on the prompter. If you started the camera and the prompter at the same
instant, --broll-at 50 is the same thing. Check the first cut and adjust
--broll-at by a second or two if your first word and the prompter differ.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / "video"
INTRO_SECONDS = 47.0


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def first_speech(path: Path, noise_db: int = -35, min_silence: float = 0.3) -> float:
    """Seconds into the file at which the audio first rises above the noise floor."""
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", out)]
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", out)]
    if not starts or starts[0] > 0.05:
        return 0.0  # sound from the very start
    return ends[0] if ends else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("camera", type=Path)
    ap.add_argument("--broll", type=Path, default=VIDEO / "broll-full.mp4")
    ap.add_argument("--out", type=Path, default=VIDEO / "kearney-recipe-pipeline.mp4")
    ap.add_argument("--broll-at", type=float, help="seconds into the camera file where the b-roll starts (default: first speech + 47)")
    ap.add_argument("--pip", choices=["small", "large", "none"], default="small", help="your picture during the b-roll")
    args = ap.parse_args()

    cam_len = duration(args.camera)
    broll_len = duration(args.broll)
    if args.broll_at is None:
        speech = first_speech(args.camera)
        args.broll_at = speech + INTRO_SECONDS
        print(f"first speech at {speech:.1f}s, b-roll starts at {args.broll_at:.1f}s")
    start, end = args.broll_at, args.broll_at + broll_len
    print(f"camera {cam_len:.0f}s, b-roll {broll_len:.0f}s, b-roll window {start:.1f}s to {end:.1f}s")

    pip_w = {"small": 420, "large": 640, "none": 0}[args.pip]
    window = f"between(t,{start:.3f},{end:.3f})"
    filters = [
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[cam]",
        f"[1:v]fps=30,setpts=PTS+{start:.3f}/TB[br]",
        f"[cam][br]overlay=0:0:enable='{window}':eof_action=pass[base]",
    ]
    if pip_w:
        filters.append(f"[0:v]scale={pip_w}:-2,setsar=1,fps=30[pip]")
        filters.append(f"[base][pip]overlay=W-w-40:H-h-40:enable='{window}':eof_action=pass[v]")
    else:
        filters[-1] = filters[-1].replace("[base]", "[v]")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-stats",
        "-i", str(args.camera), "-i", str(args.broll),
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{cam_len:.3f}",
        str(args.out),
    ]
    subprocess.run(cmd, check=True)
    print(f"wrote {args.out} ({duration(args.out):.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
