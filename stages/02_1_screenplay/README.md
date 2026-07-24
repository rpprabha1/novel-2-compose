# 02_1_screenplay

**Type:** AGENT — see `CLAUDE.md` §2 and `ARCHITECTURE.md`.

## Purpose

Dramatizes one scene's prose into a **screenplay**: an ordered list of elements
(sluglines, action, dialogue, narration, transitions). First of the three
front-end decomposition agents (screenplay → scene extraction → shot division).
The sluglines it places are what `02_2_scene_extraction` uses to find film-scene
boundaries; the action/dialogue/narration split is what shot division and
narration read.

Runs at **scene granularity** (one scene at a time), like every agent stage
here — a whole-novel screenplay in one call would exceed the local model's
context. Wired into the Coordinator behind the opt-in `screenplay_frontend`
run-config flag (default off).

## I/O

- **Input:** exactly one scene `.txt` file in `inputs/` (prose, paragraph breaks
  preserved) + `run_config` (`tone`).
- **Output:** `outputs/screenplay.json` — validates against
  `shared/schemas/screenplay.schema.json`.

## Run / test instructions

```
python stages/02_1_screenplay/src/run.py <input_dir> <output_dir> <run_config.yaml>
python -m pytest stages/02_1_screenplay/tests/
```

Tests inject a fake `agent_call`, so no live model is needed. The stage runs
against the local Ollama backend (`config/agents.yaml`) in production.

## Numeric pass criterion

`screenplay.json` validates against its schema; ≥1 `slugline` present; 100% of
`dialogue` elements carry a `character`; a scene of >150 source words yields ≥3
elements (else HITL-flagged as over-summarized).

## Review checklist

- [ ] Every element traces to real content in the source scene (no fabrication).
- [ ] Sluglines mark only genuine location/time changes.
- [ ] Interior/exposition text is `narration`, filmable action is `action`.
- [ ] `scene_id` matches the input; `run_id` is from context.

## Agent prompt

`AGENT_PROMPT.md` (all 12 `CLAUDE.md` §7 sections) is the source of truth for
agent behavior; `src/run.py` renders sections 1-6 and 9 into the system prompt.
