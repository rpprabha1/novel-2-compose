from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage072_narration_shot_mapping_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from shared.envelopes import validate_against_schema  # noqa: E402

RUN_ID = "test_072"
RUN_CONFIG = {"run_id": RUN_ID, "pacing": "standard"}
THRESHOLDS = {
    "shot_extraction": {"target_shot_length_s": 4.0, "min_shot_length_s": 1.0, "max_shots_per_beat": 12},
    "downloader_selection": {"assets_per_beat": 3},
}
VOCAB = {"pacing_presets": {"standard": {"hold_duration_s": {"min": 1.5, "max": 4.0}}}}


def _clean_run_dir():
    d = REPO_ROOT / "shared" / "runs" / RUN_ID
    if d.exists():
        shutil.rmtree(d)


def _beats(*specs):
    # specs: (beat_id, est_duration_s)
    return {
        "run_id": RUN_ID, "scene_id": "s1",
        "beats": [
            {"beat_id": bid, "order": i, "text_excerpt_ref": f"para:{i+1}",
             "visual_description": "d", "est_duration_s": est, "mood_tags": ["quiet"], "no_visual_analog": False}
            for i, (bid, est) in enumerate(specs)
        ],
    }


def _manifest(*clips):
    # clips: (clip_id, duration_s)
    return {"stage": "01_1_downloader", "clip_count": len(clips),
            "clips": [{"clip_id": cid, "file_ref": f"stages/01_1_downloader/outputs/{cid}.mp4", "duration_s": dur} for cid, dur in clips]}


def _scores(mapping):
    # mapping: {beat_id: [clip_id, ...]}
    out = []
    for beat_id, cids in mapping.items():
        ranked = [{"clip_id": cid, "file_ref": f"stages/01_1_downloader/outputs/{cid}.mp4",
                   "score": 0.3 - 0.01 * i, "rank": i + 1, "frames_scored": 3} for i, cid in enumerate(cids)]
        out.append({"beat_id": beat_id, "ranked_clips": ranked})
    return {"run_id": RUN_ID, "scene_id": "s1", "scores_by_beat": out}


def _audio_mix(mapping):
    # mapping: {beat_id: narration_duration_s}
    return {"run_id": RUN_ID, "scene_id": "s1",
            "narration_stems": [{"beat_id": b, "file_ref": f"{b}.wav", "start_s": 0.0, "duration_s": d} for b, d in mapping.items()],
            "music_stems": [], "mix_params": {"ducking_depth_db": -12, "ducking_attack_ms": 150},
            "final_lufs": -16.0, "total_duration_s": sum(mapping.values())}


def _write(input_dir, beats, scores, manifest, audio_mix=None):
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "scene_scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (input_dir / "downloader_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if audio_mix is not None:
        (input_dir / "audio_mix.json").write_text(json.dumps(audio_mix), encoding="utf-8")


def _fake_extractor():
    """Returns (trim, prober) where trim writes a stub file and prober reports
    exactly the requested window length (so hold==out==duration deterministically)."""
    lengths: dict[str, float] = {}

    def fake_trim(src, dest, in_s, length):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake mp4")
        lengths[str(dest)] = length

    def fake_probe(dest):
        return lengths[str(dest)]

    return fake_trim, fake_probe


def _run(input_dir, output_dir):
    trim, prober = _fake_extractor()
    return run.main(input_dir, output_dir, RUN_CONFIG, trim=trim, prober=prober, thresholds=THRESHOLDS, vocab=VOCAB)


def test_maps_narration_to_multiple_extracted_shots(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir,
           _beats(("b1", 3.0)),
           _scores({"b1": ["clip_001"]}),
           _manifest(("clip_001", 100.0)),
           _audio_mix({"b1": 12.0}))

    resp = _run(input_dir, output_dir)
    assert resp.status.value == "COMPLETE"

    shot_map = json.loads((output_dir / "shot_map.json").read_text(encoding="utf-8"))
    edit_plan = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))

    b1 = shot_map["beats"][0]
    # 12s narration / 4s shots -> 3 shots, distinct advancing windows.
    assert len(b1["shots"]) == 3
    ins = [s["source_in_s"] for s in b1["shots"]]
    assert ins == [0.0, 4.0, 8.0]  # distinct windows, not a frozen frame
    assert all(s["extracted_file_ref"].endswith(".mp4") for s in b1["shots"])
    # total covers the narration
    assert abs(sum(s["duration_s"] for s in b1["shots"]) - 12.0) < 1e-6

    # edit_plan shots are extract-exact (in_s=0, out_s==hold==duration)
    for shot in edit_plan["beats"][0]["shots"]:
        assert shot["in_s"] == 0.0
        assert shot["out_s"] == shot["hold_duration_s"]
    # assets are source-free
    a = assets["assets"][0]
    assert a["origin"] == "downloader"
    assert a["attribution"] == {"source": "downloader", "creator_required": False}
    assert "creator" not in a["attribution"]

    validate_against_schema(shot_map, "shot_map.schema.json")
    validate_against_schema(edit_plan, "edit_plan.schema.json")
    validate_against_schema(assets, "assets_manifest.schema.json")
    _clean_run_dir()


def test_short_narration_single_shot(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats(("b1", 3.0)), _scores({"b1": ["clip_001"]}),
           _manifest(("clip_001", 100.0)), _audio_mix({"b1": 3.0}))
    resp = _run(input_dir, output_dir)
    assert resp.status.value == "COMPLETE"
    shot_map = json.loads((output_dir / "shot_map.json").read_text(encoding="utf-8"))
    assert len(shot_map["beats"][0]["shots"]) == 1
    assert shot_map["beats"][0]["shots"][0]["duration_s"] == 3.0
    _clean_run_dir()


def test_alternates_across_multiple_ranked_clips(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats(("b1", 3.0)),
           _scores({"b1": ["clip_001", "clip_002"]}),
           _manifest(("clip_001", 100.0), ("clip_002", 100.0)),
           _audio_mix({"b1": 8.0}))
    resp = _run(input_dir, output_dir)
    shot_map = json.loads((output_dir / "shot_map.json").read_text(encoding="utf-8"))
    clip_ids = [s["source_clip_id"] for s in shot_map["beats"][0]["shots"]]
    # round-robin: alternate the two clips rather than draining one
    assert clip_ids[0] == "clip_001" and clip_ids[1] == "clip_002"
    _clean_run_dir()


def test_spreads_footage_across_beats_and_advances_reused_windows(tmp_path):
    """The real fix: on a repetitive scene where every beat ranks the SAME clips
    top, footage must not carpet the whole video with one clip. Two beats both
    ranked [c1,c2,c3]; global least-used-first selection should (a) reach clip_003
    even though no beat put it in the top-2 slot it would have been used from
    naively, (b) never place the same clip in back-to-back shots, and (c) show a
    FRESH window when a clip is reused across beats."""
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    thresholds = {
        "shot_extraction": {"target_shot_length_s": 4.0, "min_shot_length_s": 1.0,
                            "max_shots_per_beat": 12, "candidate_pool_per_beat": 3},
        "downloader_selection": {"assets_per_beat": 3},
    }
    _write(input_dir,
           _beats(("b1", 3.0), ("b2", 3.0)),
           _scores({"b1": ["clip_001", "clip_002", "clip_003"],
                    "b2": ["clip_001", "clip_002", "clip_003"]}),
           _manifest(("clip_001", 100.0), ("clip_002", 100.0), ("clip_003", 100.0)),
           _audio_mix({"b1": 8.0, "b2": 8.0}))  # 2 shots per beat
    trim, prober = _fake_extractor()
    resp = run.main(input_dir, output_dir, RUN_CONFIG, trim=trim, prober=prober, thresholds=thresholds, vocab=VOCAB)
    assert resp.status.value == "COMPLETE"

    shot_map = json.loads((output_dir / "shot_map.json").read_text(encoding="utf-8"))
    shots = [s for b in shot_map["beats"] for s in b["shots"]]
    clip_seq = [s["source_clip_id"] for s in shots]

    # (a) diversity reached all three clips, not just the top two.
    assert set(clip_seq) == {"clip_001", "clip_002", "clip_003"}
    # (b) no back-to-back repeat of the same clip.
    assert all(clip_seq[i] != clip_seq[i + 1] for i in range(len(clip_seq) - 1))
    # (c) any clip used more than once shows distinct windows (advancing cursor).
    from collections import defaultdict
    wins = defaultdict(list)
    for s in shots:
        wins[s["source_clip_id"]].append(s["source_in_s"])
    for cid, ins in wins.items():
        assert len(ins) == len(set(ins)), f"{cid} reused the same window {ins}"
    _clean_run_dir()


def test_falls_back_to_est_duration_without_audio_mix(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats(("b1", 8.0)), _scores({"b1": ["clip_001"]}),
           _manifest(("clip_001", 100.0)))  # no audio_mix
    resp = _run(input_dir, output_dir)
    assert resp.status.value == "COMPLETE"
    shot_map = json.loads((output_dir / "shot_map.json").read_text(encoding="utf-8"))
    assert shot_map["beats"][0]["narration_duration_s"] == 8.0  # from est_duration_s
    assert abs(sum(s["duration_s"] for s in shot_map["beats"][0]["shots"]) - 8.0) < 1e-6
    _clean_run_dir()


def test_beat_with_no_scored_clip_is_fallback_routed(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    # b2 has no ranked clip -> routed, but b1 still produces shots.
    _write(input_dir, _beats(("b1", 3.0), ("b2", 3.0)),
           _scores({"b1": ["clip_001"], "b2": []}),
           _manifest(("clip_001", 100.0)),
           _audio_mix({"b1": 4.0, "b2": 4.0}))
    resp = _run(input_dir, output_dir)
    assert resp.status.value == "FALLBACK_ROUTED"
    assert any(fr.reason_code == "no_scored_clip" and fr.item_id == "b2" for fr in resp.fallback_routed)
    edit_plan = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assert [b["beat_id"] for b in edit_plan["beats"]] == ["b1"]
    _clean_run_dir()


def test_all_beats_unscored_fails(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats(("b1", 3.0)), _scores({"b1": []}), _manifest())
    resp = _run(input_dir, output_dir)
    assert resp.status.value == "FAILED"
    _clean_run_dir()


def test_max_shots_cap_stretches_shot_length(tmp_path):
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    thresholds = {
        "shot_extraction": {"target_shot_length_s": 4.0, "min_shot_length_s": 1.0, "max_shots_per_beat": 3},
        "downloader_selection": {"assets_per_beat": 3},
    }
    _write(input_dir, _beats(("b1", 3.0)), _scores({"b1": ["clip_001"]}),
           _manifest(("clip_001", 100.0)), _audio_mix({"b1": 30.0}))
    trim, prober = _fake_extractor()
    resp = run.main(input_dir, output_dir, RUN_CONFIG, trim=trim, prober=prober, thresholds=thresholds, vocab=VOCAB)
    assert resp.status.value == "COMPLETE"
    shot_map = json.loads((output_dir / "shot_map.json").read_text(encoding="utf-8"))
    shots = shot_map["beats"][0]["shots"]
    assert len(shots) <= 3  # capped
    assert abs(sum(s["duration_s"] for s in shots) - 30.0) < 1e-6  # stretched to still cover
    _clean_run_dir()


def test_beat_transition_out_uses_configured_default(tmp_path):
    # Matches 07_editorial_direction's deterministic path (see ARCHITECTURE.md
    # 2026-07-23): beat-to-beat cuts use editorial_vocab.yaml's
    # default_beat_transition (a real author-requested crossfade, not a
    # uniform hard-cut) - 08_timeline_builder applies this only at each
    # beat's LAST shot regardless of what's set here.
    _clean_run_dir()
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _beats(("b1", 3.0)), _scores({"b1": ["clip_001"]}), _manifest(("clip_001", 100.0)))
    vocab = {**VOCAB, "default_beat_transition": "crossfade"}
    trim, prober = _fake_extractor()
    resp = run.main(input_dir, output_dir, RUN_CONFIG, trim=trim, prober=prober, thresholds=THRESHOLDS, vocab=vocab)
    assert resp.status.value == "COMPLETE"
    edit_plan = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assert edit_plan["beats"][0]["transition_out"] == "crossfade"
    _clean_run_dir()


def test_missing_inputs_fail(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()
    resp = _run(input_dir, output_dir)
    assert resp.status.value == "FAILED"
