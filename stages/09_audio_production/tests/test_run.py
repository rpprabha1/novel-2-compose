import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage09_audio_production_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from shared.sources import MusicCandidate  # noqa: E402

RUN_CONFIG = {"run_id": "test_run_09", "tone": "gothic-suspense", "music_intensity_curve": "flat"}


@pytest.fixture(autouse=True)
def _clean_run_cache():
    # Every test shares run_id "test_run_09" (and often beat_id "b1"), and
    # main() caches narration/music files by path and skips re-synthesizing
    # if the file already exists. Without cleaning before AND after every
    # test, a stale cached file from one test silently short-circuits a
    # later test's fake/failing callable - this bit test_tts_failure_fails
    # for real (a leftover cached b1.wav meant _raising_tts was never called).
    _clean_run_dir("test_run_09")
    yield
    _clean_run_dir("test_run_09")

AUDIO_SPEC = {
    "loudness": {"target_lufs": -16.0, "tolerance_lu": 1.0},
    "ducking": {"depth_db": -12, "attack_ms": 150},
    "crossfade": {"default_length_s": 1.0},
    "tone_music_tags": {"gothic-suspense": ["tense", "quiet", "ominous"]},
}

SCENE_TEXT = "First paragraph narration text.\n\nSecond paragraph narration text."


def _beats(beat_specs: list[tuple[str, str]]) -> dict:
    return {
        "run_id": "test_run_09",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": bid,
                "order": i,
                "text_excerpt_ref": ref,
                "visual_description": "desc",
                "est_duration_s": 3.0,
                "mood_tags": ["quiet"],
                "no_visual_analog": False,
            }
            for i, (bid, ref) in enumerate(beat_specs)
        ],
    }


def _write(input_dir: Path, beats: dict, scene_text: str = SCENE_TEXT) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "scene_text.txt").write_text(scene_text, encoding="utf-8")


def _clean_run_dir(run_id: str) -> None:
    run_dir = REPO_ROOT / "shared" / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)


def _fake_tts(text: str, dest_path: Path, duration_s: float = 1.0) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}", str(dest_path)],
        capture_output=True,
        text=True,
        check=True,
    )


def _fake_downloader(url: str, dest: Path, duration_s: float = 8.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration_s}", str(dest)],
        capture_output=True,
        text=True,
        check=True,
    )


class FakeMusicSource:
    name = "fake"

    def __init__(self, candidates: list[MusicCandidate]):
        self.candidates = candidates

    def search(self, mood_tags, max_results=3):
        return self.candidates[:max_results]


VALID_CUE_JSON = json.dumps(
    {
        "cues": [
            {
                "cue_id": "cue001",
                "start_beat_id": "b1",
                "end_beat_id": "b2",
                "mood_tags": ["tense", "quiet"],
                "target_intensity": 0.3,
                "rationale": "single sustained mood",
            }
        ]
    }
)

ONE_CANDIDATE = [
    MusicCandidate(track_ref="track1", source="fake", url="https://example.com/1", license="Fake License", download_url="https://example.com/1.mp3", creator="Some Artist")
]


def test_complete_happy_path(tmp_path):
    run_id = "test_run_09"
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1"), ("b2", "para:2")]))

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: VALID_CUE_JSON,
        music_source=FakeMusicSource(ONE_CANDIDATE),
        tts_fn=_fake_tts,
        downloader=_fake_downloader,
        audio_spec=AUDIO_SPEC,
        hitl_decisions={"cue001": "track1"},
    )

    assert response.status.value == "COMPLETE"
    assert (output_dir / "audio_mix.json").exists()
    assert (output_dir / "scene_mix.wav").exists()
    mix = json.loads((output_dir / "audio_mix.json").read_text(encoding="utf-8"))
    assert len(mix["narration_stems"]) == 2
    assert len(mix["music_stems"]) == 1
    assert mix["music_stems"][0]["selected_by"] == "human"
    assert -20 < mix["final_lufs"] < -10  # sane range around -16 target

    # Timing is audio-driven and sequential - each stem starts exactly where
    # the previous one ends, never overlapping (the real bug this fixed).
    n1, n2 = mix["narration_stems"]
    assert n1["start_s"] == 0.0
    assert n2["start_s"] == pytest.approx(n1["start_s"] + n1["duration_s"], abs=0.05)
    assert mix["total_duration_s"] == pytest.approx(n1["duration_s"] + n2["duration_s"], abs=0.05)

    manifest = json.loads((REPO_ROOT / "shared" / "runs" / run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["entries"][0]["kind"] == "music"


def test_track_selection_needs_input_without_decision(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1"), ("b2", "para:2")]))

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: VALID_CUE_JSON,
        music_source=FakeMusicSource(ONE_CANDIDATE),
        tts_fn=_fake_tts,
        downloader=_fake_downloader,
        audio_spec=AUDIO_SPEC,
    )

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "track_selection"
    assert (output_dir / "music_cue_intent.json").exists()
    assert not (output_dir / "audio_mix.json").exists()


def test_no_music_candidates_routes_fallback(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1"), ("b2", "para:2")]))

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: VALID_CUE_JSON,
        music_source=FakeMusicSource([]),
        tts_fn=_fake_tts,
        downloader=_fake_downloader,
        audio_spec=AUDIO_SPEC,
    )

    assert response.status.value == "FALLBACK_ROUTED"
    assert response.fallback_routed[0].reason_code == "no_music_candidates"


def test_no_music_source_configured_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1"), ("b2", "para:2")]))

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: VALID_CUE_JSON,
        music_source=None,
        tts_fn=_fake_tts,
        audio_spec=AUDIO_SPEC,
    )

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "no_music_source_configured"
    assert (output_dir / "music_cue_intent.json").exists()


def test_cue_coverage_gap_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1"), ("b2", "para:2")]))
    incomplete_cue = json.dumps(
        {"cues": [{"cue_id": "cue001", "start_beat_id": "b1", "end_beat_id": "b1", "mood_tags": ["tense"], "target_intensity": 0.3, "rationale": "r"}]}
    )

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: incomplete_cue,
        tts_fn=_fake_tts,
        audio_spec=AUDIO_SPEC,
    )

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "cues_incomplete"


def test_leading_gap_auto_repairs(tmp_path):
    # Regression test for the real run: llama3.2:3b reliably started its
    # single cue at b2 instead of b1, reasoning that's where the mood
    # "shifts" - leaving b1 uncovered every time (3/3 reproductions). With
    # only one cue, extending it backward to cover b1 is the only coherent
    # repair, so this should auto-resolve rather than block.
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1"), ("b2", "para:2")]))
    gapped_cue = json.dumps(
        {"cues": [{"cue_id": "cue001", "start_beat_id": "b2", "end_beat_id": "b2", "mood_tags": ["tense"], "target_intensity": 0.3, "rationale": "mood shift at b2"}]}
    )

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: gapped_cue,
        music_source=FakeMusicSource(ONE_CANDIDATE),
        tts_fn=_fake_tts,
        downloader=_fake_downloader,
        audio_spec=AUDIO_SPEC,
        hitl_decisions={"cue001": "track1"},
    )

    assert response.status.value == "COMPLETE"
    assert "Auto-repaired a cue coverage gap" in response.summary
    intent = json.loads((output_dir / "music_cue_intent.json").read_text(encoding="utf-8"))
    assert intent["cues"][0]["start_beat_id"] == "b1"
    mix = json.loads((output_dir / "audio_mix.json").read_text(encoding="utf-8"))
    assert len(mix["narration_stems"]) == 2  # both beats' narration still present


def test_invalid_mood_tag_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1")]))
    bad_cue = json.dumps(
        {"cues": [{"cue_id": "cue001", "start_beat_id": "b1", "end_beat_id": "b1", "mood_tags": ["scary"], "target_intensity": 0.3, "rationale": "r"}]}
    )

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: bad_cue,
        tts_fn=_fake_tts,
        audio_spec=AUDIO_SPEC,
    )

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "cues_incomplete"


def test_cue_mood_tag_from_beats_own_tag_is_allowed(tmp_path):
    # Regression test for a real bug: allowed_mood_tags was only
    # tone_music_tags[tone], ignoring the beats' own mood_tags entirely -
    # audio_spec.yaml's own comment documents the union as the intended
    # design. A beat legitimately tagged outside the tone's list (e.g.
    # "romantic" in a gothic-suspense scene) made every cue covering it
    # schema-unsatisfiable regardless of model quality, since the agent is
    # shown that beat's real mood_tags but validated against a narrower set.
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", "para:1")])
    beats["beats"][0]["mood_tags"] = ["romantic"]  # not in AUDIO_SPEC's gothic-suspense tone list
    _write(input_dir, beats)
    cue = json.dumps(
        {"cues": [{"cue_id": "cue001", "start_beat_id": "b1", "end_beat_id": "b1", "mood_tags": ["romantic"], "target_intensity": 0.3, "rationale": "r"}]}
    )
    candidates = [MusicCandidate(track_ref="t1", source="fake", url="https://example.com/1", license="Fake License", download_url="https://example.com/1.mp3")]

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: cue,
        music_source=FakeMusicSource(candidates),
        tts_fn=_fake_tts,
        downloader=_fake_downloader,
        audio_spec=AUDIO_SPEC,
        hitl_decisions={"cue001": "t1"},
    )

    assert response.status.value != "FAILED"
    assert not any(item.reason_code == "cues_incomplete" for item in (response.needs_input or []))


def test_tts_failure_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats([("b1", "para:1")]))

    def _raising_tts(text, dest_path):
        raise run.TTSError("simulated synthesis failure")

    response = run.main(input_dir, output_dir, RUN_CONFIG, tts_fn=_raising_tts, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"
