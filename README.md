# Recipe Enhancement Platform

Enhances recipes by applying community-tested modifications from AllRecipes reviews, with line-level attribution back to the review that motivated each change. The pipeline is the backend for a diff-inspection experience: an enhanced recipe, the changes made to it, the alternatives the community disagreed about, and the tweaks that were considered and not applied.

**Read [docs/ASSESSMENT.md](docs/ASSESSMENT.md) first.** It is the write-up for this take-home: what the inherited pipeline actually did, the evaluation that measures it, the fixes, the numbers before and after, and what I would do next.

## Setup

Python 3.13 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                       # installs dependencies and the llm_pipeline package
cp .env.example .env          # then put your key in it
```

`.env` at the repo root:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini       # optional; the default
```

## Run

All commands run from the repo root.

```bash
# See the inherited defects with no API key
uv run python scripts/repro_inherited_defects.py

# Tests (no API key)
uv run pytest

# Enhance one recipe, or all of them, into data/enhanced/
uv run python -m llm_pipeline run --recipe data/recipe_10813_best-chocolate-chip-cookies.json
uv run python -m llm_pipeline run --all

# Read an enhanced recipe as a diff
uv run python -m llm_pipeline show data/enhanced/enhanced_10813_best-chocolate-chip-cookies.json

# Evaluate the rewritten pipeline, the inherited one, or another model
uv run python -m eval.run_eval
uv run python -m eval.run_eval --legacy
uv run python -m eval.run_eval --model gpt-4.1-mini
uv run python -m eval.run_eval --compare eval/results/*.json
```

Every model response is cached under `data/cache/llm/` by request hash, and the cache is committed. Re-running the eval or the pipeline on the committed data makes no API calls; new reviews or a new model or prompt version do. `--no-cache` bypasses it.

`src/test_pipeline.py single|all` still works as a thin wrapper.

## What it does

1. **Extract, per review.** Every review is sent to the model with the numbered recipe lines and comes back as zero or more discrete modifications. Each has one category, two flags (did the reviewer actually make it, would it help someone else), and edits that reference exact recipe lines. Strict JSON schema output.
2. **Compose, per recipe.** No model call. Modifications are ranked (featured tweak first, then rating, then recency) and applied only when both flags are true. Two modifications that target the same line are a conflict: the higher-ranked one is applied and the other is attached to that line as an alternative with its source review. Everything not applied stays in the output with a reason.
3. **Emit.** `data/enhanced/enhanced_<id>_<slug>.json`, plus `pipeline_summary_report.json` for a full run. A recipe with nothing applicable still emits, with status `no_tweaks` and a reason. `failed` is reserved for genuine faults.

Output shape, abbreviated:

```json
{
  "title": "Best Chocolate Chip Cookies (Community Enhanced)",
  "ingredients": ["1 cup butter, softened", "0.5 cup white sugar", "..."],
  "instructions": ["..."],
  "modifications_applied": [
    {
      "source_review": {"text": "...", "rating": 5, "is_featured": true},
      "modification_type": "quantity_adjustment",
      "summary": "use 0.5 cup white sugar and 1.5 cups brown sugar",
      "changes_made": [
        {"type": "ingredient", "operation": "replace", "line_index": 2,
         "from_text": "1 cup packed brown sugar", "to_text": "1.5 cups packed brown sugar",
         "alternatives": [{"source_review": {"rating": 5}, "proposed_text": "0.5 cup packed brown sugar", "summary": "brown sugar 1 cup -> 1/2 cup"}]}
      ],
      "edits_failed": []
    }
  ],
  "modifications_considered": [
    {"summary": "next time add one more cup of apples", "reason": "not applied by the reviewer (stated intent or suggestion)"}
  ],
  "enhancement_summary": {"status": "enhanced", "total_changes": 10, "reviews_screened": 9, "modifications_applied": 8, "modifications_considered": 5}
}
```

## Layout

```
src/llm_pipeline/
  prompts.py            prompt, strict output schema, prompt version
  tweak_extractor.py    step 1: review -> modifications (LLM, cached)
  recipe_modifier.py    matcher: line ref -> exact -> normalized -> guarded fuzzy; per-edit status
  composer.py           step 2: rank, resolve conflicts, apply (no LLM)
  enhanced_recipe_generator.py, pipeline.py, __main__.py
  llm_client.py         OpenAI wrapper with the response cache and gpt-5 parameter mapping
  legacy/               frozen copy of the inherited pipeline, for the baseline
eval/
  cases.json            28 labelled cases (21 real reviews, 7 synthetic)
  run_eval.py, adapters.py, scoring.py
  results/              one JSON per configuration
tests/                  matcher, composer, the three inherited defects, and an
                        end-to-end test that plays every demo scene offline
scripts/
  repro_inherited_defects.py   no-key reproduction of the inherited apply-layer defects
  run_legacy_baseline.py       regenerate the inherited pipeline's outputs (seeded)
  broll.py                     the demo as numbered terminal scenes (offline, from the cache)
  render_broll.py              renders those scenes to MP4 for the video
  export_trajectory.py         session transcript to AGENT_TRAJECTORY.md
data/
  recipe_*.json         scraped input
  enhanced/             current outputs; enhanced/baseline/ holds the inherited pipeline's
  cache/llm/            committed model responses
docs/ASSESSMENT.md      the write-up
```

## Demo and video

`uv run python scripts/broll.py` plays the nine demo scenes in a terminal, pausing between them; `--scene N` replays one. `uv run python scripts/render_broll.py` renders the same scenes to `video/scene-NN.mp4` and `video/broll-full.mp4` (silent, 1080p) for the walkthrough video; `video/SCRIPT.md` is the narration. `tests/test_integration.py` runs every scene from the committed cache and asserts the outputs the script talks about, so the demo cannot drift from the code.

## Scraper

`uv run python src/scraper_v2.py` is the inherited scraper, unchanged. AllRecipes returned 403 for four of five URLs when I tried it; `data/recipe_20144_banana-banana-bread.json` is the one that succeeded. Its `has_modification` flag is regex noise and is not used by the pipeline.
