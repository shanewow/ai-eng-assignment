# Recipe Enhancement Pipeline: Assessment Report

Shane Kearney, September 2026

<!-- Sections marked TODO are filled in during the build. Keep them honest: numbers come from eval/results, not from memory. -->

## 1. Summary

TODO after the build. Three sentences: what was broken, what I did, what the numbers say now.

## 2. Assumptions

- The product is the diff-inspection experience described in the brief: a user sees an enhanced recipe and can inspect line-level changes with the community review that motivated each one. The pipeline is the backend that produces that data, so its output has to be honest enough to render. I did not build a UI.
- "Featured Tweaks" means the highest-voted, community-tested modifications. The `featured_tweaks` field in the scraped data is the closest available signal, with star rating as the tie-breaker. The scraper does not capture helpful-vote counts.
- A review can contain many discrete modifications, of different kinds. Not all of them belong in an enhanced recipe. Some are intent rather than action ("next time I will use fresh ginger"), and some are personal circumstance rather than an improvement anyone could reuse ("I used a different frosting because I had leftover"). A modification should be applied only if the reviewer actually made it and it would help someone else making this recipe.
- The scraped data in `data/` is representative of what a scraper would produce at scale, including its noise. I did not fix the scraper beyond noting its problems.
- Keeping the OpenAI provider from the starter is the right call for a four-hour exercise; graders can run it with their own key. Model choice is a measured decision, not a default (see Results).
- Four hours is a guideline for attention budgeting. I logged actual time in section 9.

## 3. Problem analysis

### 3.1 What the inherited pipeline does

`src/test_pipeline.py single|all` runs one recipe through three steps:

1. **Extract.** Pick one review at random from those flagged `has_modification`, send it with the recipe to the model, and ask for a single `ModificationObject` with one category and a list of edits.
2. **Apply.** For each edit, find the closest recipe line by fuzzy string similarity (threshold 0.6) and run a plain string replace on it.
3. **Emit.** Wrap the result with attribution and write an enhanced JSON file.

### 3.2 Does it work?

It runs, exits zero, and prints "Single recipe test passed". It does not work.

The first thing I did was run the starter's own few-shot prompt examples, the ones it shows the model as the definition of correct output, through its own apply step. No LLM call involved. The fourth example, which changes oven temperature and bake time, applied zero of two edits. The matcher compares a short phrase like `350 degrees F` against whole instruction lines, so similarity never clears the threshold. Any instruction-level tweak is silently lost.

The first live run made the second problem obvious. The random pick was a review that changed the salt, omitted the walnuts, and pressed the cookies flat before baking. The extractor returned one change, the salt. The schema only allows one, so two of three community modifications vanished and the run reported success.

### 3.3 What a full run actually produces

Running every recipe in the data set prints this:

```
Pipeline complete: 5/7 recipes successfully enhanced
All recipes validation passed! ✓
```

Five enhanced recipes, ten changes applied, zero errors reported. Here is what those five files contain.

| Recipe | What the pipeline produced | Why it is wrong |
|---|---|---|
| Nikujaga | Ingredient list contains both `0.25 cup soy sauce` and `0.5 cup soy sauce`, and both `1 tablespoon white sugar` and `2 tablespoons white sugar` | The reviewer doubled the seasonings. The model emitted an insert where it meant a replace, and nothing checked. A cook cannot tell which line to follow. |
| Spicy apple cake | The entire frosting step became `Use a different frosting leftover from another cake.` | A complete instruction was destroyed and replaced with something that is not an instruction. The source reviewer said they made the cake exactly as directed and happened to have leftover frosting. That is circumstance, not a recipe improvement. |
| Best chocolate chip cookies | One line reads `1 cup white sugar and 1/2 cup brown sugar`, while the original `1 cup packed brown sugar` line is untouched | Two ingredients were merged into one line and the other was left alone. The recipe now calls for 1.5 cups of brown sugar where the reviewer used half a cup. |
| Creamy sweet potato soup | `1.5 teaspoons ground ginger` became `Fresh grated ginger`, and `Additional chicken broth to reach desired consistency` was added to the ingredient list | The quantity was deleted outright. The broth line is an instruction sitting in the ingredients, and it came from `will use more broth next time`, which the reviewer had not done. |
| Banana banana bread | `0.75 cup brown sugar` became `0.75 cup honey` | Faithful to the review. The only defensible output of the five. |

So the answer to the assignment's question is sharper than "the pipeline has bugs." **The pipeline reports success on five recipes and corrupts four of them, and it has no way to know.** Every failure mode above is invisible from inside the system: the change records say the edits were applied, the summary counts them, and the test suite passes.

That is the shape of the real problem. The code is not missing features. It is missing any notion of whether what it did was correct.

### 3.4 Defects found

| # | Defect | How I confirmed it |
|---|--------|--------------------|
| D1 | Instruction edits never apply. Short `find` strings are compared against whole lines. | Starter's own few-shot example 4: best similarity 0.20 and 0.35, zero edits applied. |
| D2 | Silent no-op counted as a change. Fuzzy match succeeds, exact replace finds nothing, but a change record is still emitted with identical before and after text. | `find="1/2 teaspoon salt"` matches `"0.5 teaspoon salt"` at 0.82; record emitted; ingredient unchanged. |
| D3 | Ambiguous fuzzy hits corrupt the recipe. Threshold 0.6 on short strings. | `"1 egg"` resolves to `"2 eggs"` at 0.73. A replace on that line rewrites the wrong ingredient. |
| D4 | One modification and one category per review. "I added an egg and halved the sugar" cannot be represented. | Schema in `models.py`; live baseline run dropped two of three modifications. |
| D5 | One random review per recipe. Nondeterministic output, and the featured-tweak signal is never read. | `random.choice` in `tweak_extractor.py`; `featured_tweaks` unused anywhere. |
| D6 | The checked-in example output cannot be produced by this code. It has two applied modifications and a `confidence_score` field no model defines. | Compared `data/enhanced/*.json` against `models.py`. |
| D7 | Failures are swallowed. Unmatched edits are logged and dropped; the recipe still cites the review as applied. A safety validator exists and is never called. | `recipe_modifier.py` else branches; `pipeline.py`. |
| D8 | `has_modification` is regex noise. Patterns like "next time", "will make again", and "more X" flag wishes as applied changes. | Apple cake reviews: "I would prefer some more apple chunks" is flagged. |
| D9 | Prompt hygiene. Recipe injected as a Python list repr, no instruction to enumerate every change or skip hypotheticals, few-shot builder is dead code, retries resend an identical prompt, JSON mode with no schema enforcement. | `prompts.py`, `tweak_extractor.py`. |
| D10 | Path and data assumptions. Must run from `src/`; output lands in `src/data/enhanced` while the README says `data/enhanced`; prep and cook times are dropped by the `Recipe` model so they are always null; recipes with zero reviews return nothing and `all` mode still passes; conflicting tweaks across reviews have no resolution policy. | Multiple files; two of six sample recipes have no reviews. |
| D11 | No tests and no evaluation. Nothing in the repo can answer "does it work beyond a couple of examples". | Absence. |
| D12 | Insert used where replace was meant, producing contradictory duplicate ingredients. The apply step honours whatever operation the model names, with no check that the result is coherent. | Nikujaga output lists two soy sauce quantities and two sugar quantities. |
| D13 | No validation that a replacement is well formed. A replacement may delete a quantity, put an instruction in the ingredient list, or substitute prose for a cooking step. | Sweet potato ginger line lost its amount; apple cake frosting step replaced with a non-instruction. |

## 4. Approach

TODO during the build. The shape:

1. **Measure first.** Hand-label every modification review in the data set with its discrete modifications, then build an eval that scores modification recall, precision against hallucinated or hypothetical changes, edit apply rate, no-op rate, and wrong-line rate. Run it on the inherited code to get a baseline.
2. **Fix the apply layer.** Exact substring first, then normalized matching (fractions and decimals, unit synonyms, case), fuzzy only as a guarded fallback with an ambiguity check. Verify the text actually changed. Return per-edit status instead of fake change records.
3. **Fix the extraction layer.** Many modifications per review, one category each. Numbered recipe lines in the prompt. Explicit "did the reviewer actually do this" flag. Strict structured outputs.
4. **Fix selection and composition.** Process every modification review, ranked featured first then by rating. Detect conflicts per target line and keep the higher-ranked one, recording the loser as skipped with a reason.
5. **Re-measure.** Same eval, same cases, two models.

## 5. Technical decisions

Each decision below is stated as the choice, the alternative I weighed, and why I went the way I did.

### 5.1 Evaluate before fixing

**Decision.** Build a labelled evaluation set and a scoring harness first, run it against the inherited code to get a baseline, and only then change anything.

**Alternative.** Start fixing the obvious defects immediately. They are not subtle, and three hours is not much time.

**Why.** Section 3 shows a system that reports success while producing corrupted output. The root problem is not any single bug; it is the absence of any measure of correctness. Fixing bugs without a measure would reproduce exactly that failure, one level up: I would have a pipeline that looked better and no way to prove it. The harness is also the deliverable that answers the question the brief actually asks, which is whether this works beyond a couple of examples. Every fix that follows has to move a number that existed before the fix.

### 5.2 Provider stays OpenAI, model is a measured choice

**Decision.** Keep the OpenAI client from the starter. Make the model a runtime option and report evaluation results for more than one.

**Alternative.** Switch providers, or hard-code a better model than the `gpt-3.5-turbo` the starter actually calls.

**Why.** Keeping the provider means the graders can run this with their own key and no setup change. Making the model an option turns "which model" from an opinion into a row in a results table, which is the same discipline as the rest of the report. Note that the cheapest current models are reasoning models, which take `max_completion_tokens` and reject a custom temperature, so the client wrapper handles both parameter families rather than assuming one.

### 5.3 A modification is applied only if it was made and is generalizable

**Decision.** The extractor returns two booleans per modification: whether the reviewer actually made the change, and whether it would help someone else cooking this recipe. Both must be true for the change to be applied. Modifications that fail either test are kept in the output with the reason they were excluded.

**Alternative.** Apply anything the reviewer actually did, and filter only stated future intent.

**Why.** The data contains at least four distinct kinds of statement, and only some of them belong in an enhanced recipe:

| Kind | Example from the data | Apply? |
|---|---|---|
| Improvement | "I did add an additional egg yolk to help keep the cookie chewy" | Yes |
| Error correction | "1/4 lb of meat to serve 4??? I used a lil over 1lb" | Yes |
| Future intent | "will use more broth next time" | No, not done |
| Personal circumstance | "I used a different frosting only because I had some leftover" | No, not reusable |

The looser alternative would still have produced the apple cake failure, because that reviewer genuinely did use different frosting. The distinction that matters to a reader of the recipe is not whether it happened, but whether following it would help them. Keeping the excluded ones visible costs nothing and gives the product something worth showing: a panel of community tweaks that were considered and consciously not applied, each with a reason.

### 5.4 Conflicts resolve by rank, and the loser becomes an alternative

**Decision.** Rank modifications by featured status, then rating, then recency. When two modifications target the same line, apply the higher-ranked one and attach the other to that line as an alternative, with its source review.

**Alternative.** Apply the winner and log the loser as skipped, or refuse to touch contested lines at all.

**Why.** Ranked resolution is what makes the output deterministic, which the inherited random selection was not. Surfacing the loser rather than discarding it is nearly free once conflict detection exists, and it turns a limitation into the most interesting thing on the page. Two five-star reviewers disagree about the sugar ratio in the cookies. That disagreement is real information about the recipe, and a diff view that shows "we applied this one, here is the other" is more useful and more honest than one that silently picks a winner.

### 5.5 Screen every review here, design a recall gate for scale

**Decision.** Send every review to the extractor in this implementation, and let it return an empty list when there is no modification. Document the production design, which is a cheap recall-oriented filter in front of the model.

**Alternative.** Keep the scraper's `has_modification` regex as the gate, possibly widened.

**Why.** The existing regex has errors in both directions. It flags "I would prefer some more apple chunks" as a modification, and it misses reviews entirely, including one nikujaga review that has no such key at all. It also ignores `featured_tweaks`, which is the product's whole premise. At seven recipes, screening everything costs cents and removes an entire class of defect from the analysis. At two million reviews it would not be the right call, because most reviews contain no modification, so section 8 describes the gate I would put in front of it and why the filter belongs there rather than in a separate model call.

### 5.6 A recipe with no community tweaks is a result, not a failure

**Decision.** A recipe with no applicable modifications emits an enhanced record with an empty modification list and a stated reason. Failure is reserved for genuine faults.

**Alternative.** Keep the inherited behaviour, where such a recipe returns nothing and is counted as a failed recipe.

**Why.** Two of the seven sample recipes have no reviews at all. The product page still has to render for them. Conflating "there was nothing to do" with "something broke" makes the success rate meaningless, which is precisely how the inherited run manages to report five successes while corrupting four recipes.

### 5.7 Evaluation labels are model-drafted and human-verified

**Decision.** Gold labels for the evaluation set were drafted with model assistance and then reviewed and corrected by hand, and the set includes synthetic cases for edge conditions that the sample data does not cover.

**Alternative.** Hand-label everything from scratch, or skip gold labels and use a judge model to score extractions against the review text.

**Why.** Hand-labelling from scratch is the most defensible option and the most expensive, and the transcription is not where the judgment lives. Judge-model scoring with no gold set grades the student with the student, and it would have happily approved the apple cake output. Drafting then verifying keeps a human decision on every label while spending the budget on the decisions rather than the typing. Stating the method plainly is part of the result.

## 6. Implementation details and challenges

TODO during the build.

## 7. Results

TODO. Before and after eval tables, two models, with the per-recipe enhanced output linked.

## 8. Production shape: running this at scale

The brief asks whether the pipeline works beyond a handful of examples. The honest answer has two halves: whether the logic is correct, which the eval addresses, and whether the shape of the thing survives being run on every review of every recipe. This section is the second half. I pictured the pipeline as a Lambda triggered by an event such as "a new review arrived" or "a recipe was re-scraped", and asked what would have to change first.

### 8.1 Change the unit of work first

The inherited pipeline's unit of work is one recipe, one random review, one modification, one file. In an event-driven system that shape is wrong three ways: it is nondeterministic, it repeats work, and it cannot compose results across reviews. The first structural change is to split it into two stages with different cost profiles.

**Extract, per review.** The expensive step. Keyed by a content hash of recipe version, review text, prompt version, and model, and cached. A given review is never sent to the model twice. Adding a review to a recipe with fifty existing reviews costs one extraction, not fifty.

**Compose, per recipe.** No model call. Take every cached extraction for the recipe, rank them (featured first, then rating, then recency), resolve conflicts per target line, apply, and emit the enhanced recipe with its diff. Runs in milliseconds and can be re-run freely when a new extraction lands or the ranking policy changes.

In AWS terms that is two small Lambdas behind SQS rather than one large one. A new review triggers one extract and one compose. Within the take-home, the same split exists in the code: extraction results are cached to disk by hash, and composition is a pure function over them.

### 8.2 Make it safe to run unattended

- **Async, not synchronous.** Event to SQS to Lambda, with a dead-letter queue after three attempts and a visibility timeout longer than the model call timeout. The OpenAI client timeout should be set to around thirty seconds; the SDK default is ten minutes, so one hung call could consume an entire invocation.
- **Concurrency with a ceiling.** A recipe's reviews are extracted concurrently using the async client and a semaphore. Lambda reserved concurrency is capped so aggregate throughput stays inside the OpenAI tier's rate limits, and the SDK's exponential backoff handles the occasional 429.
- **Honest results.** Every edit carries a status: applied, failed, ambiguous, or skipped because of a conflict, each with a reason. A recipe with partial failures still emits, carrying its failure list, instead of returning nothing and logging success. This is the same property the diff UI needs, so it is not extra work.
- **Versioned prompts and config.** The prompt version is stamped into every output so a prompt change can be re-run selectively rather than across the whole corpus. Model name and matching thresholds come from environment or parameter store; the API key comes from a secrets manager, not a `.env` file.
- **Observability that reuses the eval.** Structured JSON logs and per-run metrics: tokens in and out, latency, modifications per review, edit apply rate, no-op rate, conflict rate. These are the same numbers the eval harness produces, so the development metric and the production dashboard are the same code path.
- **Package hygiene.** The pipeline Lambda should not ship the scraper's dependencies (BeautifulSoup, lxml, requests). Scraping is a separate job with a separate failure mode; during this exercise AllRecipes returned 403 for four of five scrape attempts. Python 3.13 runtime, a package of roughly 25 MB, client initialized at module scope, cold start under a second.

### 8.3 Cut token cost before adding compute

- **Lay the prompt out for caching.** The recipe text is identical across all of a recipe's reviews. Put it first and the review last so OpenAI's automatic prompt caching hits on the shared prefix. Cached input tokens cost roughly a tenth of uncached ones. This is a reorder, not a feature.
- **Strict structured outputs.** A JSON schema with strict mode removes the malformed-JSON retry loop entirely.
- **Batch API for backfills.** Half price with a 24-hour window. Use it for the initial corpus and for prompt-version re-runs; reserve real-time calls for newly arrived reviews.
- **The smallest model that passes the eval.** This is the practical reason to build the eval first. It turns "which model" from a guess into a measurement. Results in section 7 compare two models on the same cases.
- **No LLM pre-classifier.** It is tempting to add a cheap "is this review a modification?" call in front of extraction. An extraction that returns an empty list costs the same as a classifier call, so the pre-classifier only adds latency. A cheap, recall-oriented regex gate is enough.

### 8.4 Cost model

Per review, extraction is roughly 1,200 input tokens (recipe about 600, prompt template about 400, review about 150) and roughly 300 output tokens for the JSON plus minimal reasoning. List prices verified September 2026.

| Model | Price per 1M tokens, in / out | Per review | Per 1M reviews | With prompt caching on the recipe prefix |
|---|---|---|---|---|
| gpt-5-nano | $0.05 / $0.40 | $0.00018 | $180 | $150 |
| gpt-5-mini | $0.25 / $2.00 | $0.00090 | $900 | $800 |
| gpt-4.1-mini | $0.40 / $1.60 | $0.00096 | $960 | $830 |

The Batch API halves any of these for backfill work. Two caveats on the numbers: a regional-processing uplift applies to some models released after March 2026, and the `gpt-4o-mini` the README claims this pipeline uses has been superseded by the 4.1 line, so it is left out of the table.

**Sizing.** At AllRecipes scale, roughly 100,000 recipes with about 20 modification reviews each, the corpus is about 2 million reviews. A one-time backfill is about $360 on gpt-5-nano or $1,900 on gpt-4.1-mini, and half that through Batch. Steady state depends on review arrival rate; at 10,000 new reviews a day it is about $2 a day on nano.

**Infrastructure is noise by comparison.** Lambda at 512 MB for a five-second invocation handling ten concurrent extractions costs about two cents per thousand recipes. SQS, S3, and DynamoDB are cents per million operations. The one line item to watch is CloudWatch: logging raw model payloads at debug level, as the inherited code does, costs more than the Lambda compute at about $1.50 per million reviews of log ingestion. Log tokens and metrics in production, not payloads.

**Rule of thumb.** Model tokens are more than 95 percent of the cost of running this. The levers, in order, are cache by content hash so nothing is extracted twice, prompt caching on the recipe prefix, Batch for anything that is not latency-sensitive, and eval-driven model choice.

## 9. Future improvements

TODO after the build. Candidates, to be pruned to what I would actually do next:

- Servings-level and yield changes (a reviewer who says "1/4 lb of meat to serve 4 has to be a typo, I used a pound") have no representation in the edit model.
- Unit and quantity normalization as a first-class step, so "1/2 tsp" and "0.5 teaspoon" are the same token before matching.
- Nutrition recompute after quantity changes.
- Scraper hardening: rate limiting, retries, and a real modification classifier instead of regex.
- The diff-inspection UI, built on the per-edit status and line indices this pipeline now emits.

## 10. Time log

TODO. Actual time by phase, including the part that went over the four-hour guideline if any did, and what was cut to fit.

| Phase | Time |
|---|---|
| Diagnosis, no-LLM repro, baseline run | |
| Eval cases and harness | |
| Apply layer | |
| Extraction layer | |
| Selection and composition | |
| Re-measure, docs, video | |
