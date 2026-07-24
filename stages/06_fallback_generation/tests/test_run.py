from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage06_fallback_generation_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

RUN_CONFIG = {"run_id": "test_run_06", "tone": "gothic-suspense"}

# Stage 06 is RETIRED/DISABLED as of the 2026-07-23/24 downloader-lane cutover
# (see the stage README + ARCHITECTURE.md change log). `main()` is now a no-op
# that always returns COMPLETE and generates nothing - these tests pin that
# contract so the synthetic fallback can't quietly come back.


def test_disabled_stub_completes_and_generates_nothing(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    assert "disabled" in response.summary.lower() or "retired" in response.summary.lower()
    assert response.output_manifest == []
    # No assets_manifest.json (or anything else) is written.
    assert not (output_dir / "assets_manifest.json").exists()


def test_disabled_stub_ignores_legacy_kwargs(tmp_path):
    # Pre-cutover callers passed agent_call / image_generator / zoompan /
    # mood_visual_renderer - the stub must accept and ignore them, not error.
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    def _boom(*args, **kwargs):  # must never be called
        raise AssertionError("disabled Stage 06 must not invoke any renderer/agent")

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=_boom, image_generator=_boom, zoompan=_boom, mood_visual_renderer=_boom,
    )

    assert response.status.value == "COMPLETE"
    assert not (output_dir / "assets_manifest.json").exists()


def test_disabled_stub_completes_even_with_no_input_dir(tmp_path):
    # The stub doesn't read inputs at all - a missing input dir is not an error.
    response = run.main(tmp_path / "does_not_exist", tmp_path / "out", RUN_CONFIG)
    assert response.status.value == "COMPLETE"
