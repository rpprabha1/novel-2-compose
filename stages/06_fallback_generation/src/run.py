"""Stage 06: fallback_generation - RETIRED / DISABLED (2026-07-23).

This stage's whole job was to synthesize a placeholder visual (originally an
AGENT+diffusion sd-turbo still, later a plain ffmpeg text card, most recently a
text-free mood-colored gradient) for any beat that got no acceptable retrieved
asset. It is disabled as of the 2026-07-23 downloader-lane cutover (author
override, logged in DECISIONS_LOG.md and ARCHITECTURE.md's change log): footage
now comes exclusively from the source-free downloader lane (01_1_downloader ->
shared/downloader_manifest.py -> 01_2_scene_scoring -> shared/downloader_assets.py),
and the author's explicit instruction was to "strictly remove the backup option"
- the synthetic fallback is "not needed at all".

Per the author's "retire, keep folders" decision this stage folder is left in
place (its code stays in git history for revival), but `main()` no longer
generates anything: it is a no-op that always returns COMPLETE with no output,
so nothing downstream ever receives a generated placeholder. The prior
AGENT+diffusion and mood-visual code paths are intentionally gone from here; see
the 2026-07-23 change-log entry in ARCHITECTURE.md (and git history) to restore
them. `main()` accepts and ignores the old injectable renderer/agent kwargs so
any lingering caller keeps working (it just gets a COMPLETE no-op).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import StageResponse, StageStatus  # noqa: E402

STAGE_NAME = "06_fallback_generation"

_DISABLED_SUMMARY = (
    "Stage 06 synthetic fallback is retired/disabled (2026-07-23 downloader-lane "
    "cutover; see DECISIONS_LOG.md / ARCHITECTURE.md). No fallback assets are "
    "generated - footage comes solely from the source-free downloader lane."
)


def main(input_dir: Path, output_dir: Path, run_config: dict, *args, **kwargs) -> StageResponse:  # noqa: ARG001
    """Disabled no-op. Always COMPLETE, never generates or writes anything.

    Extra positional/keyword args (the old `agent_call`, `image_generator`,
    `zoompan`, `mood_visual_renderer`) are accepted and ignored so pre-cutover
    callers don't break.
    """
    return StageResponse(
        envelope_id="",
        run_id=run_config["run_id"],
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=_DISABLED_SUMMARY,
        output_manifest=[],
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml>")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status == StageStatus.COMPLETE else 1)
