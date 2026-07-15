# 00_coordinator

**Type:** AGENT (orchestration judgment) + CODE core — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

The only component that invokes stages, moves data between them, talks to the human, and holds run-level state. Sequencing, envelope construction, and gate enforcement are code; framing `NEEDS_INPUT`/HITL questions for the human is agent work. Full protocol: `CLAUDE.md` §5.

## I/O

- Input: `shared/runs/<run_id>/run_config.yaml` (created at run start).
- Output: Task Envelopes to every stage (schema: `shared/schemas/task_envelope.schema.json`), `shared/runs/<run_id>/coordinator_log.jsonl` (append-only), human-facing HITL summaries.

## Run / test instructions

Not yet implemented. Blocked on Gate 0 (`docs/GATE0_RESULTS.md`) recording a GO decision, per `CLAUDE.md` rule 5.

## Numeric pass criterion

Zero invocations of Stage N+1 without `outputs/APPROVED.md` present on Stage N. 100% of Stage Responses schema-validated against `expected_output_schema` before acceptance. Zero direct stage-to-stage handoffs (every artifact passes through the Coordinator).

## Review checklist

- [ ] Gate enforcement verified with a deliberately-unapproved stage (must refuse).
- [ ] `coordinator_log.jsonl` captures every envelope and response.
- [ ] HITL batching presents options + tradeoffs, never a raw dump.
