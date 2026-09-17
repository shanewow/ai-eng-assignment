"""End-to-end run of every demo scene, offline, from the committed cache.

This is the same sequence scripts/broll.py plays for the video. If it passes,
the recording will show what the report says it shows. Outputs go to a temp
directory so the committed files are untouched.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import broll  # noqa: E402


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    out = tmp_path_factory.mktemp("enhanced")
    results = tmp_path_factory.mktemp("results")
    outputs = {}
    for scene in broll.scenes(str(out), str(results)):
        code, text = broll.run_scene(scene, typing=0, pause=0.0, clear=False)
        outputs[scene.title] = (code, text, scene)
    return outputs


def test_every_scene_exits_zero(played):
    bad = {title: code for title, (code, _, _) in played.items() if code != 0}
    assert not bad, bad


def test_every_scene_shows_what_the_report_claims(played):
    missing = {}
    for title, (_, text, scene) in played.items():
        absent = [e for e in scene.expect if e not in text]
        if absent:
            missing[title] = absent
    assert not missing, missing


def test_demo_runs_offline_from_the_cache(played):
    # A cache miss would have called the API; the eval prints how many responses were cached.
    for title, (_, text, _) in played.items():
        if "Measure" in title:
            assert "'cached_responses': 28" in text, title


def test_run_summary_has_three_states(played):
    _, text, _ = played["7  Run every recipe"]
    assert "enhanced " in text and "no_tweaks " in text
    assert any(line.endswith("0 failed") for line in text.splitlines())
