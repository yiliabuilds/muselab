# MuseLab

**A modular AI visual creation workflow — Creative Inspiration Lab**

MuseLab turns a reference image into a structured, machine-readable design record, then renders
it under constraints and audits the result. It is built as a Claude/WorkBuddy **Skill**: a set of
routers, JSON Schemas, deterministic Python tools and domain rules that a host model loads on
demand.

It is not an image generator, and it is not a prompt library. The generator is a dependency; the
work in this repository is what sits on both sides of it — turning pixels into measured data,
turning that data into a design specification, and checking whether the render actually obeyed
the specification.

```
Reference image ─► measured palette ─► structured design ─► constrained render ─► audit
                   (CIELAB k-means)     (Schema + versions)   (one of three routes)   (code + human)
```

**Status: v1.3.1, pre-public research prototype.** Jewelry and makeup are implemented and have
been rendered for real. Outfit and IP-derivative modules are *not* implemented — see
[Roadmap](#10-roadmap). Real-render acceptance is currently 0/4; that number is explained in
[Validation](#8-validation-and-testing) rather than hidden.

---

## 1. Project overview

Most "AI design" work fails in the same three places:

1. **The reference is never actually measured.** Colours come from a description of an image, not
   from the image. Two people looking at the same photo produce two different palettes.
2. **The design lives in prose.** By the third revision nobody knows which paragraph is current,
   and the prompt is updated while the layer description is not.
3. **Nobody checks the output against the intent.** The render looks plausible, so it ships — and
   the bead that was never in the photo is now in the catalogue.

MuseLab is a response to those three failures, in that order. Every design fact is either measured
or explicitly marked as estimated; every design is a versioned JSON record, not a paragraph; and
every render is followed by a check that reports what it could and could not verify.

### Why a Skill and not a web app

The unit of work is a *judgement* — which route to take, whether a deviation is a material error
or a lighting artefact, whether a region is even testable. Those decisions need a model with
vision, not a form. The deterministic parts (colour measurement, alpha compositing, schema
validation, colour arithmetic, version control) are Python and are tested. The split is deliberate:
**the model decides, the scripts measure, and neither is allowed to claim the other's work.**

## 2. Key features

| # | Feature | State |
|---|---|---|
| 1 | Modular skill architecture with capability-aware routing between three generation routes | Implemented |
| 2 | Pixel-level palette extraction (CIELAB k-means, measured area shares, measured-vs-estimated flag) | Implemented, tested |
| 3 | Eight-dimension visual analysis → domain design rules (jewelry, makeup) | Implemented |
| 4 | Structured design specification: JSON Schema, version state machine, immutability of approved versions | Implemented, tested |
| 5 | Cross-field consistency audit (palette ↔ layers ↔ steps ↔ prompt ↔ concept) | Implemented, tested |
| 6 | Image quality gate (background spec, hue/material separation, pixel integrity, mask audit) | Implemented, tested on synthetic + 1 real render |
| 7 | Makeup-specific checks (eyelid coverage and climb, three-signal left/right balance, under-lid pigment vs specular, lip boundary) | Implemented, Beta thresholds |
| 8 | Single-source prompt with derived mirror and drift detection (`prompt-sync`) | Implemented, tested |
| 9 | Product compositing route: real cutout → pixel-integrity-checked composite, no generative model | Implemented, tested |
| 10 | Outfit design, IP-derivative design, virtual try-on | **Not implemented** |

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Host model (vision + reasoning)                                      │
│   reads images · chooses a route · declares ROIs · judges semantics  │
└───────────────┬──────────────────────────────────────────────────────┘
                │ SKILL.md routing table
    ┌───────────┴────────────┬─────────────────┬──────────────────┐
    ▼                        ▼                 ▼                  ▼
workflows/               references/       schemas/           scripts/
visual-analysis          jewelry rules     design-spec        color_extract
color-extraction         makeup rules      makeup-spec        cutout / compose
jewelry-design           photography       image-task        pixel_integrity
makeup-design            color theory      visual-profile    mask_audit
collection-design        api_reference     project-state     fidelity_check
generation-modes         module-extension                     makeup_check
virtual-try-on (spec)                                         makeup_spec_audit
                                                              muselab_state
```

**Deterministic layer (Python, tested).** Colour measurement, alpha compositing, pixel-integrity
comparison, mask audit/repair, gate arithmetic, spec auditing, project state and versioning.
Twelve scripts, no network calls, no model calls.

**Judgement layer (the host model).** Reading an image, selecting the generation route, declaring
regions of interest, deciding whether a deviation is acceptable, and writing the design record
itself.

**The contract between them** is the JSON design record: the model writes it, the scripts validate
and version it, the audit checks it for internal contradiction, and the prompt is derived from it.

### Three generation routes

| Route | Mechanism | What it can promise |
|---|---|---|
| **A. Product compositing** | Real cutout (Vision alpha) → pixel-integrity-checked composite | The retained region keeps its original pixels, and that is verified; overall accuracy is still bounded by mask quality |
| **B. Creative generation** | Text-to-image from the design record | Plausible concept imagery; structure and count are *not* guaranteed |
| **C. Reference-based generation** | Image editing from a supplied reference | Background/lighting changes with subject preservation — **empirically the weakest**: it invented out-of-frame structure |

Routing is capability-aware: if the host has no image tool, the workflow degrades to a prompt
deliverable and says so, rather than pretending to render.

## 4. Workflow

```
1  Ingest        image or brief
2  Measure       palette (k-means), visual profile (8 dimensions) — measured vs estimated always labelled
3  Design        domain rules → design record (JSON), schema-validated
4  Audit         makeup_spec_audit.py --strict  (cross-field contradictions, drift, scope of change)
5  Render        route A / B / C, one call, no silent retry
6  Check         fidelity_check.py · makeup_check.py · pixel_integrity.py  → verdict + what it could not judge
7  Review        human/vision pass on the items the scripts refuse to score
8  Revise        new version with change_request; approved versions are never edited in place
```

Two invariants hold across the whole pipeline:

- **Approved versions are immutable.** A revision creates version N+1 with a `change_request` and
  a `parent_version`; history is append-only.
- **A check never announces more than it measured.** Every gate report carries a
  `needs_human_or_vision` list and an `automation_boundary` block.

## 5. Installation

Requirements: Python 3.9+, Pillow ≥ 10, numpy ≥ 1.24. Optional: `jsonschema`, and
`pyobjc-framework-Vision` on macOS 14+ for native alpha cutout.

```bash
git clone <this-repo> MuseLab
cd MuseLab
python3 -m pip install -r scripts/requirements.txt
python3 scripts/check_env.py            # reports which capabilities exist here
python3 tests/run_tests.py              # 90 cases, ~60 s
```

As a WorkBuddy / Claude Skill, copy the directory into the user-level skills folder:

```bash
cp -r MuseLab ~/.workbuddy/skills/muselab     # user-level
# or:  <project>/.workbuddy/skills/muselab    # project-level
```

The skill is loaded by description; no server, no daemon, no API key of its own. The host supplies
vision and image generation.

## 6. Usage examples

### Measure a palette

```bash
python3 scripts/color_extract.py --image ref.jpg --k 8 --json palette.json --swatch palette.png
```

`palette.json` records `method: pixel_kmeans`, per-cluster CIELAB and hex, and the pixel share of
each cluster. If the image cannot be measured, the method becomes `visual_estimate` — it never
silently upgrades a guess to a measurement.

### Register a design and render it

```bash
python3 scripts/muselab_state.py project-init --dir work/proj
python3 scripts/muselab_state.py design-add   --dir work/proj --file design.json
python3 scripts/muselab_state.py design-select --dir work/proj --id jw_a --version 1
python3 scripts/muselab_state.py design-approve --dir work/proj --id jw_a --version 1 --consent
# host model calls its image tool once, then:
python3 scripts/muselab_state.py task-add  --dir work/proj --design-id jw_a --file task.json
python3 scripts/muselab_state.py task-done --dir work/proj --task-id img_1 --path out.png --tool ImageGen
```

### Audit a makeup spec before spending a render

```bash
python3 scripts/makeup_spec_audit.py --project work/proj --design mk_tidal --strict
# exit 7 on any error: undeclared colour, stale concept, prompt/mirror drift, out-of-scope change
```

### Check a render (ROIs declared by a human or a vision model)

```bash
python3 scripts/makeup_check.py --config gate.json --json qa.json --overlay qa.png
python3 scripts/fidelity_check.py --config gate_jewelry.json --json qa_jewelry.json
```

### Keep the prompt single-sourced

```bash
python3 scripts/muselab_state.py prompt-sync --dir work/proj --check   # exit 6 if drifted
python3 scripts/muselab_state.py prompt-sync --dir work/proj           # creates a new version
```

## 7. Demonstrations

Full case notes — input, workflow, output, status, deviation — in [`demos/README.md`](demos/README.md).

| Case | What it shows | Status |
|---|---|---|
| Palette from pixels | 5/5 known patches recovered with method and area share | Verified |
| Palette → jewelry design | Three schema-valid design records from one palette | Verified (state machine); design quality is human |
| Jewelry concept render | Gate caught an off-spec background (ΔE 24.4) while clearing the structure | **Failed acceptance** |
| Product fidelity | Model invented the out-of-frame segment and added 2 beads | **Failed** — the instructive failure |
| Makeup v11 → v13 | v11 shipped with a stale `concept`, and the audit at that point only read colour; v12 rewrote the text, v13 passed audit E 0 / W 0 and still drifted in hue | **Both failed** — range on v11, hue on v13 |
| QA overlays | Where the numbers come from; why "it reaches the brow" was a misread | Verified as instrument |

<p align="center">
  <img src="demos/images/03_jewelry_concept_ai.jpg" width="46%" alt="AI-generated bracelet concept (watermarked)">
  <img src="demos/images/06_makeup_render_v13.jpg" width="46%" alt="AI-generated makeup render (watermarked)">
</p>

*Both images are **AI-generated** and watermarked by the host tool. They are concept renders, not
photographs of real products or real people.*

## 8. Validation and testing

```
PASS 83 | FAIL 0 | NOT-RUN 7 | ENV-UNSUPPORTED 0 | total 90
```

- **NOT-RUN is never counted as a pass.** Seven cases need an image tool, render credits, a real
  face photo or live web search; each is listed with its reason.
- **Real renders are reported separately.** Four ImageGen calls were made across three projects;
  **none of the four passed acceptance**. The gates caught the deviation in all four.
- **Threshold calibration is stated.** Eyelid thresholds rest on 4 synthetic fixtures and 2 real
  renders — enough to be useful, not enough to claim generality.

Details, including which claims are Beta and which are unverified:
[`docs/VALIDATION.md`](docs/VALIDATION.md).

## 9. Known limitations

1. **No face landmark library.** Every ROI is declared by a human or a vision model.
   `--capabilities` reports `available: []` and `roi_source: human_or_vision_declared`. A misplaced
   ROI will still produce a confident-looking number — an ROI placed on open water once returned
   `consistent` with a 9.7° hue delta.
2. **The spec audit is a dictionary and a structural diff, not semantic understanding.** It catches
   "the concept says the opposite of the layer"; it misses "the same thing said differently", and
   it misses stale wording its vocabulary does not cover.
3. **Colour fidelity on low-chroma targets is poor.** Two makeup renders drifted 35–45° into the
   wrong colour family. Prompt wording did not fix it; the reference-image route is untested.
4. **Left/right balance is confounded by lighting.** The ΔE includes L\*, so an asymmetric key light
   reads as an asymmetric application. The report says so rather than picking a winner.
5. **Identity preservation cannot be verified.** Not with these tools. It is listed as
   `not_code_testable` in every makeup report.
6. **Generated images carry the host tool's watermark.** Not suitable for publication as finished
   product photography.
7. **Outfit and IP-derivative modules do not exist.** Only the extension contract does
   (`references/module-extension.md`).
8. **Compositing accuracy is bounded by the mask, and pixel integrity only checks part of it.**
   `pixel_integrity.py` verifies that retained pixels were not repainted or cropped — not that the
   cutout was complete. Case 03i is the proof: the mask missed 1,305 px of product, `mask_audit`
   flagged it as `review`, and `pixel_integrity` still returned `pass_exact` for the pixels it could
   see. A clean pixel-integrity verdict is not a clean composite.

## 10. Roadmap

**Implemented (v1.3.x)** — palette measurement, visual profile, jewelry design, three generation
routes, product compositing with pixel integrity, design state machine, cross-field audit, makeup
module with eyelid/under-lid/lip checks, single-source prompt with drift detection.

**Next (not started)**
- Reference-image makeup route — the one intervention likely to fix the hue drift.
- Face-landmark adapter (optional dependency; falls back to declared ROIs).
- Eyewear / bag modules.
- Collection-level consistency across a multi-image set.
- Perceptual (SSIM/LPIPS) comparison instead of Laplacian energy for texture retention.

**Explicitly out of scope for now** — outfit design, IP-derivative design, virtual try-on,
autonomous multi-agent orchestration. They appear in `workflows/` at most as interface
conventions and must not be described as features.

## Repository layout

```
SKILL.md              routing table the host model reads first
workflows/            step-by-step procedures (10)
references/           domain rules and the full CLI reference (9)
schemas/              5 JSON Schemas
scripts/              deterministic tools (12)
tests/                run_tests.py, fixture generator, fixtures, RESULTS.md
demos/                curated public demo cases + images
docs/                 validation status, security scan, publish manifest
```

## License

MIT — see [LICENSE](LICENSE). Reference photographs used during development are not included.
