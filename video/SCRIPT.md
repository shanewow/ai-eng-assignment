# Video script

Target 6:00 to 6:30, under the 7:00 cap. Two kinds of footage: **to camera** (you, recorded separately) and **b-roll** (`video/scene-NN.mp4`, rendered by `scripts/render_broll.py`; `video/broll-full.mp4` is all nine in order). Each b-roll scene types its command, streams the output, then holds on the final frame for the seconds shown, so the narration fits without cuts. Freeze the last frame in the editor if you run long.

Every segment is timed from its word count at 2.1 words a second, a comfortable pace; the b-roll holds and the prompter both derive from the text below, so edit the words here and re-render both. `video/timing.txt` has the resulting lengths.

---

## 0:00  To camera (no b-roll)

> Hi, I'm Shane Kearney, and this is my walkthrough of the Casper Studios take-home. One thing up front: I did the diagnosis and made the calls, but a coding agent did most of the building from a written plan, and its transcript is in the repo. The product improves recipes by applying the highest-voted community tweaks from AllRecipes, with line-level diffs showing what was applied and why. I'll show what the inherited pipeline actually does, the evaluation built to measure it, and the rewrite, with numbers before and after. Short answer: it runs, it reports success, and it corrupts most of what it touches.

---

## Scene 1 — It fails on its own examples

Talk over the output as it appears. Point at three things.

> Before spending anything on API calls, I asked a cheaper question. The starter shows the model four examples of correct output. What happens if I feed those back through its own code? Example four: two edits intended, zero applied. The repo's own example fails through the repo's own matcher, because it compares a short phrase against a whole instruction line. Section B: the match succeeds, the replace changes nothing, and it still records a change. Section C: "one egg" resolves to "two eggs", so a replace rewrites the wrong ingredient. No API key was used for any of that.

## Scene 2 — What the inherited pipeline wrote

> Here is a real output. The reviewer used a whole cup of white sugar and half a cup of brown. The pipeline merged both into the white sugar line and left the brown sugar line alone. The recipe now calls for one and a half cups of brown sugar. And this run reported success.

## Scene 3 — The tests

> Those three defects are pinned as tests. They failed before any fix.


## Scene 4 — Measure the inherited pipeline

> That is why I did not start by fixing bugs. Fixing without a measure reproduces the same failure one level up: something that looks better with no way to prove it. I labelled every review in the data set with the discrete changes it actually contains, whether the reviewer really made them, and whether they'd help anyone else. Plus synthetic cases for the edge conditions the data misses. Then I scored the inherited pipeline. It delivers fifty-seven percent of the modifications it should, and applies sixty-five percent of the ones it should not: wishes, and personal circumstances like "I used margarine because that's what I had."

## Scene 5 — Measure the rewrite

> Same cases, same scorer, the rewritten pipeline. Ninety-three percent delivered. False-apply down to twenty-four percent. Zero silent no-ops. Every response is cached by content hash, so this run made no API calls, and anyone can reproduce these numbers without a key.

## Scene 6 — Before and after, four configurations

> The three fixes. Extraction: one modification per review was the schema. Now a review returns many, each with two flags: did the reviewer actually do this, and would it help anyone else. Both must be true to apply. Application: exact match first, then normalized for fractions and units, fuzzy only as a guarded fallback, and every edit reports applied, failed, or ambiguous. Selection: every review is ranked, featured first, then rating. And the eval caught something: the cheapest model, gpt-5-nano, finds everything and applies everything. One hundred percent false-apply. Without the measurement, that column looks like the winner.

## Scene 7 — Run every recipe

> The full run. Every review screened, not one random pick. Three honest states: enhanced, no tweaks, failed. Two recipes have no reviews; the inherited code counted those as failures. The apple cake found modifications and applied none, because neither was both done and generalizable, and it says so.

## Scene 8 — One line, three opinions

> This is the product moment. Three reviewers disagree about the dairy in this soup. The highest-ranked change is applied, and the other two are attached to the same line as alternatives, with their ratings and their reviews. Below, the modifications that were considered and not applied, each with a reason: "next time I'll use more broth" is stated intent, not a change. The old pipeline picked one review at random and hid everything else. This is what the diff view was always supposed to show.

## Scene 9 — Eight modifications from one recipe

> The cookies. Nine reviews screened, eight modifications applied, ten line changes. One reviewer listed four tweaks in one sentence; all four are here. Two conflicts resolved by rank and shown as alternatives. And the exclusions are visible, including one I disagree with: the model decided "I omitted the water" was not generalizable. It is on the page with its reason, where a person can overrule it. The old pipeline would have dropped it silently.

---

## Outro  To camera (no b-roll)

> The brief asks whether this works beyond a few examples, so I costed it as a Lambda triggered per review. Extraction is per review and cached by hash, so nothing is extracted twice; composition is per recipe with no model call. Two million reviews backfill for a few hundred dollars. What I did not do: no UI, no deploy, no scraper rewrite. Those are in the write-up with reasons. The thing worth the time was making the system able to tell whether it is right.

---

## Timing

See `video/timing.txt` after rendering. The cap is 7:00; if the total goes over, cut words rather than speeding up.

## Recording with the prompter

`uv run python scripts/render_prompter.py` writes `video/prompter.mp4`: a 3-second countdown, then every caption at the moment it should be spoken, on the same timeline as the b-roll plus the intro and outro. Start your camera recording and play the prompter at the same time; read each caption as it appears (the next one is dimmed underneath, the bar shows how long you have). `video/timing.txt` says where the b-roll starts on that timeline and where each scene begins, and `video/narration-broll.srt` places the same captions on the `broll-full.mp4` timeline for the editor. Re-render the b-roll first if the code or holds change, then the prompter.

## Assembling the final cut

Save the camera recording as `video/camera.mov`, then:

```
uv run python scripts/assemble_video.py video/camera.mov
```

It finds your first word, starts the b-roll one intro-length later (from `video/timing.json`), keeps you full-frame for the intro and outro, shows the b-roll full-frame with you picture-in-picture in between, uses your audio throughout, and writes `video/kearney-recipe-pipeline.mp4`. Watch the first cut; if the b-roll lands a second early or late, pass `--broll-at <seconds>` and run it again. `--pip none` drops your picture during the b-roll, `--pip large` makes it bigger.

## Recording notes

- Render fresh after any code change: `uv run python scripts/render_broll.py`. The integration test (`uv run pytest tests/test_integration.py`) checks the scenes still show what the script says.
- To re-render one scene with a longer hold: `uv run python scripts/render_broll.py --scene 8 --hold 45`.
- The b-roll is silent and 1920x1080 at 15 fps. Overlay your camera clip picture-in-picture for the scene sections, full frame for intro and outro.
- Export the final cut at 1080p as `kearney-recipe-pipeline.mp4`.
