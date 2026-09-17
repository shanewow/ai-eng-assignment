"""Print the title and ingredient list of a recipe or enhanced-recipe JSON file.

    uv run python scripts/print_ingredients.py data/enhanced/baseline/enhanced_10813_best-chocolate-chip-cookies.json
"""

import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data["title"])
print()
for i, line in enumerate(data["ingredients"]):
    print(f"  I{i:<2} {line}")
