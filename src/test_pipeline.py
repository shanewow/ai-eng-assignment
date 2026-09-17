#!/usr/bin/env python3
"""Thin wrapper kept for compatibility with the original README.

    uv run python src/test_pipeline.py single   # chocolate chip cookies
    uv run python src/test_pipeline.py all      # every recipe in data/

Works from any directory. The real entry point is `python -m llm_pipeline`.
"""

import sys
from pathlib import Path

from llm_pipeline.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
COOKIES = ROOT / "data" / "recipe_10813_best-chocolate-chip-cookies.json"

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    common = ["--output-dir", str(ROOT / "data" / "enhanced")]
    if mode == "single":
        sys.exit(main(["run", "--recipe", str(COOKIES), *common]))
    elif mode == "all":
        sys.exit(main(["run", "--all", "--data-dir", str(ROOT / "data"), *common]))
    else:
        print("usage: test_pipeline.py [single|all]", file=sys.stderr)
        sys.exit(2)
