# 10_human_review_gate

**Type:** CODE + human — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2. No agent involvement.

## Purpose

Generates a contact-sheet HTML (thumbnail grid + timeline scrub markers + audio cue markers) from `timeline.json` and `audio_mix.json` for human review. The human approves (writing `outputs/APPROVED.md`) or requests changes, which route back through the Coordinator to the relevant upstream stage.

## I/O

- Input: `inputs/timeline.json`, `inputs/audio_mix.json`.
- Output: `outputs/contact_sheet.html`; `outputs/APPROVED.md` written by the human (or Claude on explicit instruction) on approval.

## Run / test instructions

Not yet implemented. Blocked on Gate 0.

## Numeric pass criterion

Contact sheet renders 100% of shots and cue markers with 0 missing thumbnails.

## Review checklist

- [ ] Sheet accurately reflects `timeline.json` — no stale cache of a prior version.
- [ ] Approval/rejection routes back through the Coordinator, never edits stage outputs directly.
