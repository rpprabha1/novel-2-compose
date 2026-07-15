# 07_editorial_direction

**Type:** AGENT — fully specified in `CLAUDE.md` §4 and `ARCHITECTURE.md` §2 (not repeated here, that's the source of truth).

## Purpose

Given approved beats + winning assets + run config (tone, pacing), decides shot subdivision, hold durations, and transitions per beat boundary — creative judgment constrained entirely by `config/editorial_vocab.yaml`.

## I/O

- Input: `inputs/beats.json`, `inputs/assets_manifest.json`, `run_config.yaml`.
- Output: `outputs/edit_plan.json` (schema: `shared/schemas/edit_plan.schema.json`).

## Run / test instructions

Implemented, using `shared/agents/` (Ollama, same as Stages 02/06):

```
python -m pytest stages/07_editorial_direction/tests/ -v   # mocked agent, no model calls

python stages/07_editorial_direction/src/run.py \
  stages/07_editorial_direction/inputs \
  stages/07_editorial_direction/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, agent_call=None, thresholds=None, vocab=None)` — all three externals are injectable for testing.

A near-miss `hold_duration_s` (within `config/thresholds.yaml`'s `editorial.hold_duration_clamp_tolerance_pct` of the active pacing preset's range) is coerced to the nearest bound instead of blocking — a small local model's numeric imprecision, not a genuine editorial violation; the range itself is never widened, and values further outside still block on `NEEDS_INPUT`. See `DECISIONS_LOG.md` 2026-07-14 for why.

## Numeric pass criterion

0 transitions outside `config/editorial_vocab.yaml`'s `transition_families`; 0 hold durations outside the active pacing preset's range (after clamp-tolerance coercion); total runtime drift ≤ `config/thresholds.yaml`'s `editorial.max_runtime_drift_pct`, or explicit human approval if it exceeds that.

**Result (2026-07-14, `llama3.2:3b` via Ollama, against Stage 05's real 5-asset output): PASS.** `edit_plan.json` has all 5 beats, 1 shot each, all transitions valid (`hard-cut` x3, `crossfade` x1, `dip-to-black` x1), 0 hold durations outside range (1 near-miss auto-clamped: `ch1_sc1_b004` 2.25s → 2.5s). The real run also hit a genuine `runtime_drift` HITL trigger (15.5s actual vs. 19.5s beat-plan estimate, 20.5% > 15% limit) — approved as-is by human review (`DECISIONS_LOG.md`): the 19.5s figure was Stage 02's rough per-beat *estimate*, not a binding target, and the tighter pacing is a reasonable editorial read. 10/10 unit tests pass, including two regression tests for real bugs caught during this run: (1) `agent_call` was never defaulted to `_default_agent_call`, so omitting it crashed with `'NoneType' object is not callable` — every other test happened to pass a mock explicitly, so none caught it; (2) the clamp-tolerance behavior itself (near-miss clamps, far-miss still blocks).

**Known gap, not yet enforced in code:** the real run's `crossfade` and `dip-to-black` transitions both have an empty `rationale`, despite `AGENT_PROMPT.md` instructing the model to write one for any non-default transition. The schema allows an empty string so this doesn't currently block — worth adding a code-level check if this recurs.

## Review checklist

- [ ] Every non-default choice has a one-line rationale — **not currently true on the real run** (see Known gap above).
- [x] Beats with an asset shorter than `editorial.min_viable_shot_length_s` are flagged `NEEDS_INPUT` (reason `asset_too_short`), never stretched/looped (unit-tested; not exercised on the real run since no asset was that short).
- [x] HITL triggers fire per `CLAUDE.md` §4 (>3 shots from one asset; repeated "dramatic" transition on adjacent beats; >15% runtime drift — this one fired for real and was resolved).
