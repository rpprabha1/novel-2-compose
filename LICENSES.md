# LICENSES.md — Approved Sources and Terms

This is the authoritative allow-list backing the Source Policy hard rule in `CLAUDE.md` §0. No stage's `FootageSource`/`MusicSource` implementation may reference a source not listed here. New sources require a human-approved `ARCHITECTURE.md` change-log entry **and** a row here before any code references them.

## Footage sources

| Source | License type | Attribution required | API available | Notes |
|---|---|---|---|---|
| Pexels | Pexels License (free for commercial/non-commercial use) | No | Yes (pexels.com/api) | No endorsement implied; cannot resell unmodified. |
| Pixabay | Pixabay License (free for commercial use) | No | Yes (pixabay.com/api/docs) | Cannot redistribute standalone on another stock platform. |
| Mixkit | Mixkit Free License | No | No official public API — HTML scraping not permitted; manual/curated download only until an API exists | Free stock video; check per-clip terms, some Premium-only. |
| Coverr | Coverr Free License | No | No official public API — same manual-only caveat as Mixkit | Free stock video. |
| Archive.org | Varies per item — **only Public Domain / CC0 items** are permitted | Depends on item; verify per-fetch | Yes (archive.org/advancedsearch.php + metadata API) | Must filter to `rights:(public domain)` or explicit CC0; reject anything ambiguous. |
| Wikimedia Commons | Varies per file — typically CC-BY, CC-BY-SA, CC0, or Public Domain | Yes for CC-BY/CC-BY-SA; No for CC0/PD | Yes (commons.wikimedia.org API) | Capture exact license + creator per file at fetch time; CC-BY-SA carries share-alike obligations — flag for human review if used. |
| NASA | NASA Media Usage Guidelines (generally public domain in the US) | Recommended, not always required | Yes (images-api.nasa.gov) | Some assets include third-party content (e.g. contractor footage) — check `"NASA_id"` metadata for exceptions. |
| Author's own library | Author-owned, no restriction | No | N/A (local file access via `shared/sources/`) | Only the author's own prior work; never third-party footage placed in this library. |

## Music sources

| Source | License type | Attribution required | API available | Notes |
|---|---|---|---|---|
| Pixabay Music | Pixabay License (free for commercial use) | No | **No** — corrected 2026-07-14; Pixabay's public API covers images/videos only, there is no documented music endpoint despite pixabay.com/music existing as a browsable site. Manual/curated download only until this changes. | Same terms family as Pixabay footage; original LICENSES.md row claiming API availability was never verified and was wrong. |
| Mixkit (music) | Mixkit Free License | No | No official public API — manual/curated only | Free stock music. |
| Generated / composed audio | Fully owned, no restriction | No | N/A (via generation backend in `06`/`09` code) | Output of the pipeline's own generation step, not a third-party fetch. |
| Jamendo | Creative Commons, per-track variant (CC-BY / CC-BY-NC / CC-BY-SA etc. - exact `license_ccurl` captured per track at fetch time) | **Yes** - all CC variants require attribution; artist name recorded in the run manifest and emitted to `CREDITS.md` | Yes (api.jamendo.com v3.0, free client_id) | Added 2026-07-23 (human-approved, see `ARCHITECTURE.md` change log) - the first approved music source with a real search API. NC-variant tracks constrain commercial use of the produced video: verify each selected track's variant before commercial release. |

## Forbidden sources (hard rule, CLAUDE.md §0)

No stage may fetch from: YouTube (any tier, including Creative Commons-marked videos — terms of service prohibit third-party redistribution regardless of the video's stated license), any streaming service (Netflix, Disney+, etc.), any commercial stock/music catalog requiring a paid license, or any source containing third-party copyrighted film/TV/music content. If a stage's design would require one of these, that is a signal to escalate to the human for an `ARCHITECTURE.md` pivot — not to add the source unilaterally.

> **⚠️ Project-specific override (2026-07-24, `DECISIONS_LOG.md` + `ARCHITECTURE.md` change log).** The author has **explicitly overridden the YouTube prohibition for this project**: the source-free downloader lane (`01_1_downloader`, a YouTube tool → `shared/downloader_manifest.py` → `01_2_scene_scoring` → `shared/downloader_assets.py`) is now the pipeline's sole footage source. This override was authorized by the author after Claude flagged the conflict and refused to integrate it silently; the author accepts that YouTube's terms prohibit third-party redistribution and that any **published** output carries the corresponding third-party-copyright risk. The lane is deliberately **source-free** — it attaches no platform, url, channel, uploader, creator, or license to any clip (which is why YouTube is *not* added to the `FootageSource` allow-list above: the downloader lane is not a `FootageSource` and records no source). The general prohibition still stands for any future `FootageSource`/`MusicSource` implementation; only this specific, logged, source-free lane is exempted.

## Adding a new source

1. Confirm the license explicitly permits reuse in this pipeline's context (produced video may be published).
2. Add a row to this file with the exact license terms and attribution requirement.
3. Add a change-log entry in `ARCHITECTURE.md` §4: date, source, why, who approved.
4. Only then implement the `FootageSource`/`MusicSource` adapter in `shared/sources/`.
