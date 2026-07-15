# GATE0_RESULTS.md — Manual Coverage Test

Procedure and pass criterion defined in `ARCHITECTURE.md` §3. This file records one Gate 0 run.

## Run info

- Date: 2026-07-14
- Scene used: `shared/fixtures/sample_scene.txt` (synthetic placeholder) — **swap to a real manuscript excerpt and re-run this test before treating the GO decision below as final for production use.**
- Performed by: Claude (web search against the approved source list, in place of a human clicking through each site's UI — see note below); GO confirmed by rpprabha1@gmail.com on 2026-07-14.
- **Method note:** searches were run via web search restricted to `pexels.com`, `pixabay.com`, `mixkit.co`, `archive.org`, `commons.wikimedia.org`. For several beats this surfaced a specific named candidate clip; for others it surfaced the category/search-results page with a result count, and the judgment reflects the plausibility of finding a good match within that pool rather than a single confirmed clip. Stage 03/04's real CLIP-similarity scoring is what will make the actual per-beat pick — this test is feasibility only, not final selection.

## Beat breakdown

Suggested starting decomposition of the fixture scene into visual beats (adjust freely — this is exactly the judgment call Stage 02 will later automate, so treat it as a first draft, not a given):

| Beat | Visual description |
|---|---|
| b001 | Narrow attic staircase, dust motes lit by a shaft of light from a high cracked window |
| b002 | Hand trailing along a dusty bannister |
| b003 | Attic door creaking open onto sheeted furniture standing in rows |
| b004 | An old trunk with green, aged brass latches sitting alone under a window |
| b005 | Kneeling, opening the trunk; curling photographs and a browned letter inside |
| b006 | Close-up: handwritten letter, ink faded to tea-brown |
| b007 | A floorboard creak; a startled turn |
| b008 | An orange cat picking across a dusty light shaft toward a window ledge |
| b009 | Cat sitting on the ledge, staring out at a darkening garden |
| b010 | Letter tucked into a coat; a last look back at the trunk before the door shuts |

## Per-beat search log

| Beat | Search terms tried | Best candidate (title / URL) | Source | License | Judgment (good / marginal / none) |
|---|---|---|---|---|---|
| b001 | "narrow attic staircase dust motes light shaft" | ["Leak, Light, Dust"](https://pixabay.com/videos/leak-light-dust-film-overlay-270109/) (dust-in-light-shaft overlay) + [Attic videos](https://www.pexels.com/search/videos/attic/) (83 clips) + [Dust Motes videos](https://pixabay.com/videos/search/dust%20motes/) (536 clips) | pixabay / pexels | Pixabay License / Pexels License | good |
| b002 | "hand trailing dusty wooden bannister stairs" | [Wooden Stairs videos](https://pixabay.com/videos/search/wooden%20stairs/) (752 clips) + [Hand Movement videos](https://pixabay.com/videos/search/hand%20movement/) (3,829 clips) — no single clip combining both found | pixabay | Pixabay License | marginal |
| b003 | "old attic door creaking open sheeted furniture" | [Old Attic videos](https://pixabay.com/videos/search/old%20attic/) (1,313 clips) + [Old Furniture videos](https://pixabay.com/videos/search/old%20furniture/) (1,332 clips) — "sheeted/covered furniture" specifically not confirmed as a distinct result | pixabay | Pixabay License | marginal |
| b004 | "old trunk chest brass latches window light" | [Brass Items videos](https://pixabay.com/videos/search/brass%20items/) (40 clips, narrow) + [Vintage Old videos](https://pixabay.com/videos/search/vintage%20old/) (2,637 clips) + abundant window-light footage | pixabay | Pixabay License | marginal |
| b005 | "kneeling opening trunk old photographs letter" | [Opening Letter videos](https://pixabay.com/videos/search/opening%20letter/) (993 clips) + [Old+Letters videos](https://pixabay.com/videos/search/old+letters/) (10 clips, narrow) — no clip found combining kneeling + trunk + photographs as one compound action | pixabay | Pixabay License | marginal |
| b006 | "close up old handwritten letter faded ink" | [Person handwriting a letter](https://mixkit.co/free-stock-video/person-handwriting-a-letter-49557/) (named clip) + [Letter videos](https://mixkit.co/free-stock-video/letter/) (112 clips) | mixkit | Mixkit Free License | good |
| b007 | "woman startled turning around scared reaction" | Mixkit's fear/scary collection includes a clip explicitly described as "a young woman exploring a haunted mansion at night, when she suddenly turns around quickly being scared by something" — near-exact match. [Mixkit Fear videos](https://mixkit.co/free-stock-video/fear/) + [Pexels Startled Reaction videos](https://www.pexels.com/search/videos/startled%20reaction/) (7,746 clips) | mixkit / pexels | Mixkit Free License / Pexels License | good |
| b008 | "orange cat walking sunlight indoors" | [Cat, Orange Cat, Domestic Cat](https://pixabay.com/videos/cat-orange-cat-domestic-cat-cute-179212/) (named clip) + [Orange Cat videos](https://pixabay.com/videos/search/orange%20cat/) (3,411 clips) | pixabay | Pixabay License | good |
| b009 | "cat sitting window ledge looking out garden dusk" | [Cat Sitting videos](https://pixabay.com/videos/search/cat%20sitting/) (955 clips, video) — "ledge" + "dusk garden" specificity only confirmed in photo results, not video | pixabay | Pixabay License | marginal |
| b010 | "tucking letter into coat pocket closing door" | [Door Closing videos](https://www.pexels.com/search/videos/door%20closing/) (large collection) + [Hand In Pocket videos](https://www.pexels.com/search/videos/hand%20in%20pocket/) — no single clip combining letter+pocket+door-close as one compound action | pexels | Pexels License | marginal |

## Coverage

- Total beats: 10
- `good`: 4 (b001, b006, b007, b008)
- `marginal`: 6 (b002, b003, b004, b005, b009, b010)
- `none`: 0
- Coverage `(good + marginal) / total`: **100%**
- Clean-match rate `good / total`: **40%**

## Decision

Criterion (from `ARCHITECTURE.md` §3 / `config/thresholds.yaml: gate0_min_coverage_pct`): GO requires coverage ≥ 70% **and** zero `none` beats.

- **Decision: GO** — confirmed. 100% coverage, 0 dead-end beats.
- Rationale: Every beat has at least a plausible source-pool match. The 40% clean-match rate (vs. 100% coverage) confirms the architecture's two-lane design is doing real work here, not just theory: roughly 60% of beats in this scene are compound/specific enough that they'll likely need either careful multi-source editorial assembly or the fallback-generation lane rather than a single clean retrieved clip. That's consistent with, not a contradiction of, the design in `ARCHITECTURE.md` — Stage 06 exists precisely for this gap. No beat came back completely empty across all five sources.
- Caveat / follow-up: this run used a synthetic placeholder scene and category-level search evidence rather than individually opened/verified clips. Re-run against a real manuscript excerpt before relying on this as the final production go-ahead, and treat the "marginal" beats as an early signal to keep an eye on fallback-generation lane quality once Stage 06 is built.
- This decision satisfies CLAUDE.md Rule 5 — pipeline implementation may now proceed, starting with Stage 01.
